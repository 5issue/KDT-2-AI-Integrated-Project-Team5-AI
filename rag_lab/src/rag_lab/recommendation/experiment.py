"""상황 세트를 돌리고 결과를 남기는 **실험 인프라**. 서빙 경로가 아닙니다.

`rag-lab recommend` 가 쓰는 코드입니다. 실제 서비스 요청은 `core.recommendation_reason`
하나만 부르면 되고, 여기 있는 리포트·JSONL·재채점은 프롬프트를 비교하기 위한 것입니다.

질문 세트 실험(`rag_lab/experiment.py`)과 **파일을 나눠 둡니다.** 그쪽은 라우팅·검색
실험을 하는 사람이 계속 고치는 파일이라, 같은 파일을 양쪽에서 건드리면 충돌합니다.
결과 JSONL 형식과 파라미터 스냅샷 규칙(`snapshot_params`)은 공유합니다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rag_lab.clients import ChatClient
from rag_lab.config import Settings, get_settings
from rag_lab.experiment import snapshot_params
from rag_lab.recommendation.checks import Check, check_reason, collect_vocabulary
from rag_lab.recommendation.core import (
    MAX_REASON_CHARS,
    RecommendationCase,
    case_fingerprint,
    recommendation_reason,
)
from rag_lab.recommendation.judge import PASS_MEAN_SCORE, RUBRIC_ITEMS, RUBRIC_KEYS, RubricScore, score_reason


def load_recommendation_cases(path: Path) -> list[RecommendationCase]:
    """상황 세트 JSONL 을 읽습니다."""
    cases: list[RecommendationCase] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_no} JSON 파싱 실패: {exc.msg}") from exc

            case_id = str(payload.get("case_id") or f"line{line_no}")
            if case_id in seen:
                raise ValueError(f"{path.name}:{line_no} case_id 가 중복입니다: {case_id}")
            seen.add(case_id)

            cases.append(
                RecommendationCase(
                    case_id=case_id,
                    recipe=str(payload["recipe"]),
                    match_rate=float(payload.get("match_rate", 0.0)),
                    have=[str(value) for value in payload.get("have", [])],
                    missing=[str(value) for value in payload.get("missing", [])],
                    pantry=[str(value) for value in payload.get("pantry", [])],
                    cook_time_min=payload.get("cook_time_min"),
                    note=str(payload.get("note", "")),
                )
            )
    if not cases:
        raise ValueError(f"상황 세트가 비어 있습니다: {path}")
    return cases


@dataclass(slots=True)
class ReasonResult:
    """상황 하나의 결과."""

    case_id: str
    recipe: str
    reason: str
    elapsed_ms: float
    # 이 문구를 만들 때의 상황 지문. 재채점이 같은 상황인지 확인합니다.
    case_hash: str = ""
    checks: list[Check] = field(default_factory=list)
    rubric: RubricScore | None = None

    @property
    def checks_passed(self) -> bool:
        """자동 검사를 전부 통과했는가."""
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> list[str]:
        """실패한 검사 이름."""
        return [check.name for check in self.checks if not check.passed]


@dataclass(slots=True)
class ReasonReport:
    """추천 문구 실험 한 번의 결과."""

    name: str
    started_at: str
    params: dict[str, object]
    results: list[ReasonResult] = field(default_factory=list)

    @property
    def check_pass_rate(self) -> float:
        """자동 검사를 전부 통과한 문구의 비율."""
        return sum(r.checks_passed for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def _scored(self) -> list[RubricScore]:
        """채점에 성공한 결과만. 파싱 실패는 품질 지표에서 뺍니다."""
        return [r.rubric for r in self.results if r.rubric is not None and r.rubric.mean is not None]

    @property
    def judge_pass_rate(self) -> float | None:
        """평균 {PASS_MEAN_SCORE} 점 이상을 받은 문구의 비율. 채점을 안 돌렸으면 None."""
        scored = self._scored
        return sum(bool(s.passed) for s in scored) / len(scored) if scored else None

    @property
    def mean_score(self) -> float | None:
        """전체 평균 점수."""
        scored = self._scored
        return sum(s.mean or 0.0 for s in scored) / len(scored) if scored else None

    @property
    def item_means(self) -> dict[str, float]:
        """항목별 평균. **어느 축이 약한지 여기서 보입니다.**

        총점만 보면 프롬프트를 어디로 고쳐야 할지 모릅니다. 쪼갠 이유가 이것입니다.
        """
        scored = self._scored
        if not scored:
            return {}
        return {key: sum(s.scores[key] for s in scored) / len(scored) for key in RUBRIC_KEYS}

    @property
    def unscored(self) -> int:
        """채점 자체가 실패한 건수. 품질 실패와 섞지 않습니다."""
        return sum(1 for r in self.results if r.rubric is not None and r.rubric.mean is None)

    @property
    def distinct_ratio(self) -> float:
        """서로 다른 문구의 비율. 낮으면 틀에 박힌 문장을 찍어내고 있습니다(계획 4-3)."""
        return len({r.reason for r in self.results}) / len(self.results) if self.results else 0.0

    @property
    def failure_counts(self) -> dict[str, int]:
        """어느 검사가 몇 번 걸렸는지."""
        counts: dict[str, int] = {}
        for result in self.results:
            for name in result.failed_checks:
                counts[name] = counts.get(name, 0) + 1
        return counts

    def render(self) -> str:
        """사람이 읽을 요약."""
        lines = [
            f"실험        : {self.name}",
            f"파라미터    : {self.params}",
            f"케이스      : {len(self.results)}건",
            f"자동 검사   : {self.check_pass_rate:.1%} 통과",
            f"문구 다양성 : {self.distinct_ratio:.1%} (1.0 이면 전부 다른 문장)",
        ]
        if (mean := self.mean_score) is not None:
            rate = self.judge_pass_rate or 0.0
            lines.append(f"루브릭 평균 : {mean:.2f} / 10  (통과선 {PASS_MEAN_SCORE})")
            lines.append(f"통과율      : {rate:.1%}")
            labels = {key: label for key, label, _ in RUBRIC_ITEMS}
            detail = "  ".join(f"{labels[key]} {value:.1f}" for key, value in self.item_means.items())
            lines.append(f"항목별      : {detail}")
        if self.unscored:
            lines.append(f"채점 실패   : {self.unscored}건 (품질 실패와 다릅니다)")
        if counts := self.failure_counts:
            detail = ", ".join(f"{name} {count}건" for name, count in sorted(counts.items()))
            lines.append(f"자동 실패   : {detail}")
        return "\n".join(lines)

    def write_jsonl(self, directory: Path) -> Path:
        """결과를 JSONL 로. 첫 줄이 메타데이터인 것은 질문 세트 실험과 같습니다."""
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.replace(":", "").replace("-", "")
        path = directory / f"{stamp}_{self.name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            meta = {
                "name": self.name,
                "started_at": self.started_at,
                "kind": "recommendation_reason",
                "params": self.params,
                "check_pass_rate": round(self.check_pass_rate, 4),
                "judge_pass_rate": self.judge_pass_rate,
                "mean_score": self.mean_score,
                "item_means": {key: round(value, 2) for key, value in self.item_means.items()},
                "unscored": self.unscored,
                "distinct_ratio": round(self.distinct_ratio, 4),
            }
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for result in self.results:
                handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        return path


async def run_reason_experiment(
    name: str,
    cases: Sequence[RecommendationCase],
    *,
    chat: ChatClient,
    judge: ChatClient | None = None,
    settings: Settings | None = None,
    max_chars: int = MAX_REASON_CHARS,
) -> ReasonReport:
    """상황 세트를 한 번 돌립니다.

    `judge` 를 주면 상식 검수까지 돕니다. 없으면 자동 검사만 합니다 - 검수는 케이스당
    LLM 호출이 한 번 더 늘어서, 프롬프트를 손보는 동안에는 빼고 돌리는 편이 빠릅니다.
    """
    settings = settings or get_settings()
    vocabulary = collect_vocabulary(cases)
    report = ReasonReport(
        name=name,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        params={
            **snapshot_params(settings),
            "max_chars": max_chars,
            # **설정이 아니라 실제로 주입된 클라이언트를 적습니다.**
            # 클라이언트는 밖에서 받으므로 `Settings` 와 다를 수 있고, 그러면 결과
            # 파일이 돌지도 않은 모델 이름을 달고 남습니다. 판정자 비교가 이 이름에
            # 기대고 있어서 틀리면 비교 자체가 무의미해집니다.
            "chat_model": chat.model_id,
            "judge_model": judge.model_id if judge is not None else None,
            "pass_mean_score": PASS_MEAN_SCORE if judge is not None else None,
        },
    )

    for case in cases:
        started = time.perf_counter()
        reason = await recommendation_reason(case, chat=chat)
        elapsed_ms = (time.perf_counter() - started) * 1000

        result = ReasonResult(
            case_id=case.case_id,
            recipe=case.recipe,
            reason=reason,
            elapsed_ms=elapsed_ms,
            case_hash=case_fingerprint(case),
            checks=check_reason(case, reason, vocabulary=vocabulary, max_chars=max_chars),
        )
        if judge is not None:
            result.rubric = await score_reason(case, reason, chat=judge)
        report.results.append(result)

    return report


@dataclass(slots=True)
class PriorReason:
    """지난 실행이 남긴 문구 하나."""

    reason: str
    # 그때의 상황 지문. 옛 결과 파일에는 없어서 빈 문자열일 수 있습니다.
    case_hash: str = ""


def load_reasons(path: Path) -> dict[str, PriorReason]:
    """지난 결과 JSONL 에서 `case_id -> 문구(+상황 지문)` 를 꺼냅니다.

    **판정자를 비교하려면 같은 문구를 다시 재야 합니다.** 생성은 매번 달라지므로,
    판정자만 바꿔 새로 돌리면 문구 차이와 판정자 차이가 섞여 무엇 때문에 점수가
    바뀌었는지 알 수 없습니다.
    """
    reasons: dict[str, PriorReason] = {}
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if line_no == 1 and "results" not in payload and "case_id" not in payload:
                continue  # 첫 줄은 메타데이터입니다
            case_id, reason = payload.get("case_id"), payload.get("reason")
            if case_id and reason:
                reasons[str(case_id)] = PriorReason(str(reason), str(payload.get("case_hash") or ""))
    if not reasons:
        raise ValueError(f"문구가 들어 있지 않습니다: {path}")
    return reasons


async def rescore_experiment(
    name: str,
    cases: Sequence[RecommendationCase],
    reasons: dict[str, PriorReason],
    *,
    judge: ChatClient,
    settings: Settings | None = None,
    max_chars: int = MAX_REASON_CHARS,
) -> ReasonReport:
    """이미 만들어 둔 문구를 **다시 채점만** 합니다. 생성 호출이 없습니다.

    판정자 교차 검증용입니다. 점수가 판정자에 얼마나 좌우되는지는 같은 문구를
    두 판정자에게 보여 줘야만 알 수 있습니다.
    """
    settings = settings or get_settings()
    vocabulary = collect_vocabulary(cases)
    report = ReasonReport(
        name=name,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        params={
            **snapshot_params(settings),
            "max_chars": max_chars,
            # 재채점은 생성을 안 하므로 `chat_model` 은 지난 실행의 값이 아니라
            # **비워 둡니다.** 설정값을 적으면 이번에 그 모델이 돈 것처럼 읽힙니다.
            "chat_model": None,
            "judge_model": judge.model_id,
            "pass_mean_score": PASS_MEAN_SCORE,
            # 생성을 안 했다는 것을 기록에 남깁니다. 지연 수치가 채점만의 값입니다.
            "rescored": True,
        },
    )

    if missing := [case.case_id for case in cases if case.case_id not in reasons]:
        raise ValueError(f"지난 결과에 없는 케이스입니다: {', '.join(missing[:5])}")

    # **id 가 같아도 내용이 바뀌었으면 거부합니다.** id 를 그대로 둔 채 재료나
    # 조리시간을 고치면, 옛 문구를 새 상황으로 채점하게 됩니다. 그 점수는 판정자
    # 비교에 쓸 수 없는데 결과 파일만 보면 멀쩡해 보입니다.
    drifted = [
        case.case_id
        for case in cases
        if reasons[case.case_id].case_hash and reasons[case.case_id].case_hash != case_fingerprint(case)
    ]
    if drifted:
        raise ValueError(
            f"상황이 바뀐 케이스입니다: {', '.join(drifted[:5])}. "
            "지난 문구는 다른 상황에서 나온 것이라 다시 채점해도 비교할 수 없습니다. "
            "문구를 새로 만들거나(--rescore 없이), 케이스를 원래대로 되돌리세요."
        )

    # 옛 결과 파일에는 지문이 없습니다. 그때는 확인할 방법이 없으므로 그 사실을
    # 기록에 남깁니다. 조용히 통과시키면 나중에 그 결과를 믿게 됩니다.
    verified = all(reasons[case.case_id].case_hash for case in cases)
    report.params["case_hash_verified"] = verified

    for case in cases:
        prior = reasons[case.case_id]
        started = time.perf_counter()
        rubric = await score_reason(case, prior.reason, chat=judge)
        report.results.append(
            ReasonResult(
                case_id=case.case_id,
                recipe=case.recipe,
                reason=prior.reason,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                # **지문이 없으면 비워 둡니다.** 여기서 현재 케이스 지문을 찍으면,
                # 이 결과를 다시 재채점할 때 그 값이 "원본 지문" 으로 읽혀
                # 확인한 적 없는 문구가 검증된 것으로 둔갑합니다.
                case_hash=prior.case_hash,
                checks=check_reason(case, prior.reason, vocabulary=vocabulary, max_chars=max_chars),
                rubric=rubric,
            )
        )
    return report
