"""3단계: 추출된 재료명을 기존 ingredient 마스터의 id 로 해석.

`ingredient` 는 K-FIND 코드 체계로 큐레이션된 마스터(736행)라 파이프라인이 새 행을
만들지 않습니다. 여기서 하는 일은 2단계가 뽑은 한국어 재료명을 마스터 id 로 잇는 것뿐입니다.

두 단계로 나눕니다.
1. 정확 일치: normalized_name / name / aliases 가 그대로 맞는 것. 공짜라 먼저 씁니다.
2. 나머지: 마스터 목록을 통째로 주고 LLM Batch 가 후보와 확신도를 고릅니다.
   이름을 여러 개 묶어 한 요청에 담아 마스터 목록 토큰을 나눠 씁니다.

확신도가 임계값(MATCH_MIN_CONFIDENCE) 미만이면 채택하지 않고 미매칭으로 보고합니다.
마스터를 오염시키느니 사람이 보고 결정하는 편이 낫다는 판단입니다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

from data_pipeline.batch.client import BatchRunner, build_chat_request
from data_pipeline.config import Settings, get_settings
from data_pipeline.db import engine_scope
from data_pipeline.domain import SAFETY_RULES, ingredient_match_key, load_terminology
from data_pipeline.schemas import IngredientMatchBatch
from data_pipeline.stages import STAGE_RESOLVE, canonical

MATCH_SYSTEM_TEMPLATE = """\
너는 신선식품 재료명을 사내 재료 마스터에 연결하는 담당자다.

{safety}

{terminology}

작업:
- 주어진 재료명 각각에 대해, 아래 마스터 목록에서 가장 알맞은 재료를 하나 고른다.
- 마스터에 없으면 억지로 고르지 말고 ingredient_id 를 null 로 둔다.
  마스터는 원재료 위주라 김치, 두부, 밥 같은 가공식품은 없을 수 있다. 없으면 없다고 답한다.
- 상위/하위 관계가 있으면 더 구체적인 쪽을 고른다. 예: '소면' 이 있으면 '국수' 대신 '소면'.
- 표기만 다르고 같은 재료면 매칭한다. 예: '대파' 와 '파', 'unsalted butter' 와 '버터'.
- 성격이 다르면 매칭하지 않는다. 예: '두부' 를 '대두' 로 잇지 않는다(가공 단계가 다르다).
- confidence 는 정직하게 준다. 애매하면 0.5 이하로 준다.
- **source_name 에는 요청의 `normalized_name` 을 글자 그대로 돌려준다.**
  `display_name` 이나 원문 표기를 쓰면 어느 요청에 대한 답인지 알 수 없어 버려진다.

[재료 마스터 목록] id | 이름 | 기본형
{master}
"""

MATCH_USER_TEMPLATE = """\
아래 재료명들을 매칭해라. 요청한 개수만큼 matches 를 돌려준다.

<data>
{names}
</data>
"""


@dataclass(slots=True)
class NameRequest:
    """매칭을 요청할 재료명 하나."""

    normalized_name: str
    display_name: str
    sample_raw_text: str
    occurrence: int


@dataclass(slots=True)
class MatchResult:
    """확정된 매칭 하나."""

    normalized_name: str
    ingredient_id: int
    matched_name: str
    method: str
    confidence: float


@dataclass(slots=True)
class ResolveReport:
    """3단계 결과 요약."""

    total_names: int = 0
    exact: list[MatchResult] = field(default_factory=list)
    llm: list[MatchResult] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)

    @property
    def matched(self) -> list[MatchResult]:
        """정확 일치 + LLM 매칭."""
        return [*self.exact, *self.llm]

    def render(self) -> str:
        """사람이 읽을 요약."""
        rate = len(self.matched) / self.total_names if self.total_names else 0.0
        lines = [
            f"매칭 대상 재료명 : {self.total_names}종",
            f"정확 일치        : {len(self.exact)}종",
            f"LLM 매칭         : {len(self.llm)}종",
            f"미매칭           : {len(self.unmatched)}종",
            f"매칭률           : {rate:.1%}",
        ]
        if self.unmatched:
            lines.append("\n마스터에 없어 건너뛴 재료 (검토 필요):")
            for item in self.unmatched[:30]:
                lines.append(
                    f"  {item['normalized_name']:<16} {item.get('occurrence', 0):>4}회  "
                    f"conf={item.get('confidence', 0):.2f}  {str(item.get('reason', ''))[:50]}"
                )
            if len(self.unmatched) > 30:
                lines.append(f"  ... 외 {len(self.unmatched) - 30}종")
        return "\n".join(lines)


def collect_names(records_dir: Path) -> list[NameRequest]:
    """2단계 산출물 전체에서 매칭이 필요한 재료명을 모읍니다."""
    seen: dict[str, NameRequest] = {}

    def add(normalized: str, display: str, raw: str) -> None:
        key = ingredient_match_key(normalized or "")
        if not key:
            return
        if key in seen:
            seen[key].occurrence += 1
            return
        seen[key] = NameRequest(
            normalized_name=key,
            display_name=(display or key).strip(),
            sample_raw_text=(raw or display or key).strip(),
            occurrence=1,
        )

    for path in sorted(records_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            for item in payload.get("ingredients", []):
                add(item.get("normalized_name", ""), item.get("name", ""), item.get("raw_text", ""))
            if "normalized_name" in payload and "rules" in payload:
                add(
                    payload.get("normalized_name", ""),
                    payload.get("food_name_ko", ""),
                    payload.get("source_food_name", ""),
                )

    return sorted(seen.values(), key=lambda item: (-item.occurrence, item.normalized_name))


async def fetch_master(settings: Settings | None = None) -> list[dict[str, Any]]:
    """재료 마스터 전체를 읽습니다. 736행 정도라 프롬프트에 통째로 들어갑니다."""
    async with engine_scope(settings=settings) as engine:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT ingredient_id, name, normalized_name, aliases, parent_ingredient_id "
                        "FROM ingredient ORDER BY ingredient_id"
                    )
                )
            ).mappings()
            return [dict(row) for row in rows]


async def fetch_match_lookup(settings: Settings | None = None) -> dict[str, int]:
    """마스터 전체를 매칭 키 -> ingredient_id 로. 이름·기본형·별칭을 모두 담습니다.

    상품명에서 재료를 유추할 때 씁니다. 같은 키가 여럿이면 낮은 id 를 씁니다
    (상위 항목이 먼저 들어와 있어 더 일반적인 재료가 잡힙니다).
    """
    lookup: dict[str, int] = {}
    for row in await fetch_master(settings):
        for candidate in (row.get("normalized_name"), row.get("name"), *(row.get("aliases") or [])):
            key = ingredient_match_key(str(candidate or ""))
            if key:
                lookup.setdefault(key, int(row["ingredient_id"]))
    return lookup


async def exact_match(
    names: Sequence[NameRequest], settings: Settings | None = None
) -> tuple[list[MatchResult], list[NameRequest]]:
    """정확 일치로 붙는 것부터 처리합니다. 중복 이름은 상위 항목 > 낮은 id 순으로 고릅니다."""
    if not names:
        return [], []

    async with engine_scope(settings=settings) as engine:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "WITH wanted AS (SELECT UNNEST(CAST(:names AS text[])) AS normalized_name) "
                        "SELECT DISTINCT ON (w.normalized_name) "
                        "       w.normalized_name, i.ingredient_id, i.name, "
                        "       CASE WHEN LOWER(BTRIM(i.normalized_name)) = w.normalized_name THEN 0 "
                        "            WHEN LOWER(BTRIM(i.name)) = w.normalized_name THEN 1 ELSE 2 END AS rank "
                        "FROM wanted w JOIN ingredient i "
                        "  ON LOWER(REGEXP_REPLACE(i.normalized_name, '\\s', '', 'g')) = w.normalized_name "
                        "  OR LOWER(REGEXP_REPLACE(i.name, '\\s', '', 'g')) = w.normalized_name "
                        "  OR EXISTS (SELECT 1 FROM UNNEST(i.aliases) a "
                        "             WHERE LOWER(REGEXP_REPLACE(a, '\\s', '', 'g')) = w.normalized_name) "
                        "ORDER BY w.normalized_name, rank, (i.parent_ingredient_id IS NULL) DESC, i.ingredient_id"
                    ),
                    {"names": [item.normalized_name for item in names]},
                )
            ).mappings()
            hits = {row["normalized_name"]: row for row in rows}

    matched = [
        MatchResult(
            normalized_name=item.normalized_name,
            ingredient_id=int(hits[item.normalized_name]["ingredient_id"]),
            matched_name=str(hits[item.normalized_name]["name"]),
            method="exact",
            confidence=1.0,
        )
        for item in names
        if item.normalized_name in hits
    ]
    remaining = [item for item in names if item.normalized_name not in hits]
    return matched, remaining


def render_master(master: Sequence[dict[str, Any]]) -> str:
    """프롬프트에 넣을 마스터 목록. 한 줄에 하나씩 압축해서 씁니다."""
    lines = []
    for row in master:
        aliases = row.get("aliases") or []
        alias_text = f" | 별칭 {','.join(aliases)}" if aliases else ""
        lines.append(f"{row['ingredient_id']}|{row['name']}|{row['normalized_name']}{alias_text}")
    return "\n".join(lines)


def build_requests(
    names: Sequence[NameRequest],
    master: Sequence[dict[str, Any]],
    *,
    settings: Settings | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """미매칭 이름들을 청크로 묶어 요청을 만듭니다."""
    settings = settings or get_settings()
    system = MATCH_SYSTEM_TEMPLATE.format(
        safety=SAFETY_RULES,
        terminology=load_terminology(settings.terminology_path),
        master=render_master(master),
    )

    requests: list[dict[str, Any]] = []
    key_map: dict[str, list[str]] = {}
    chunk = settings.match_chunk_size

    for index, start in enumerate(range(0, len(names), chunk)):
        part = names[start : start + chunk]
        custom_id = f"match-{index:04d}"
        key_map[custom_id] = [item.normalized_name for item in part]
        payload = [
            {
                # source_name 으로 그대로 돌려받아야 하는 값입니다. 이름을 분명히 해 둡니다.
                "normalized_name": item.normalized_name,
                "hint_display": item.display_name,
                "hint_raw_text": item.sample_raw_text,
            }
            for item in part
        ]
        requests.append(
            build_chat_request(
                custom_id,
                system=system,
                user=MATCH_USER_TEMPLATE.format(names=json.dumps(payload, ensure_ascii=False, indent=2)),
                model=settings.openai_batch_model,
                schema_name="ingredient_match_batch",
                schema_model=IngredientMatchBatch,
            )
        )
    return requests, key_map


def matches_path(settings: Settings | None = None) -> Path:
    """3단계 중간 산출물 경로."""
    settings = settings or get_settings()
    return settings.artifacts_dir / "ingredient_matches.json"


def save_report(report: ResolveReport, settings: Settings | None = None) -> Path:
    """매칭 결과를 저장합니다. 적재 단계가 이 파일을 읽습니다."""
    settings = settings or get_settings()
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = matches_path(settings)
    payload = {
        "matched": {
            item.normalized_name: {
                "ingredient_id": item.ingredient_id,
                "matched_name": item.matched_name,
                "method": item.method,
                "confidence": item.confidence,
            }
            for item in report.matched
        },
        "unmatched": report.unmatched,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def count_llm_matches(settings: Settings | None = None) -> int:
    """저장된 리포트에 LLM 이 채운 매칭이 몇 종인지. 없으면 0."""
    path = matches_path(settings)
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    return sum(1 for item in payload.get("matched", {}).values() if item.get("method") == "llm")


def load_report(settings: Settings | None = None) -> ResolveReport:
    """저장된 3단계 산출물을 리포트로 되읽습니다. collect 가 여기에 LLM 결과를 얹습니다."""
    path = matches_path(settings)
    if not path.exists():
        raise FileNotFoundError(f"매칭 결과가 없습니다: {path.name}. resolve 를 먼저 실행하세요.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    report = ResolveReport(unmatched=list(payload.get("unmatched", [])))
    for name, item in payload.get("matched", {}).items():
        result = MatchResult(
            normalized_name=name,
            ingredient_id=int(item["ingredient_id"]),
            matched_name=str(item.get("matched_name", "")),
            method=str(item.get("method", "exact")),
            confidence=float(item.get("confidence", 1.0)),
        )
        (report.exact if result.method == "exact" else report.llm).append(result)
    report.total_names = len(report.matched) + len(report.unmatched)
    return report


def load_matches(settings: Settings | None = None) -> dict[str, int]:
    """정규화 이름 -> ingredient_id."""
    path = matches_path(settings)
    if not path.exists():
        raise FileNotFoundError(f"매칭 결과가 없습니다: {path.name}. 3단계를 먼저 끝내세요.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {name: int(item["ingredient_id"]) for name, item in payload["matched"].items()}


def _build_aliases(runner: BatchRunner, job_name: str, requested: set[str]) -> dict[str, str]:
    """요청에 함께 실어 보낸 표기들 -> 원래 매칭 키.

    모델이 `normalized_name` 대신 `hint_display` 를 돌려주는 일이 실제로 있었습니다.
    (`리큐르` 를 물었는데 `오렌지 풍미 리큐르` 로 답함) 그대로 두면 어느 요청에 대한
    답인지 몰라 버려지고, 그 재료는 매칭 기회 자체를 잃습니다.
    """
    aliases: dict[str, str] = {}
    for path in sorted(runner.requests_dir.glob(f"{job_name}_part*_input.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            body = json.loads(line).get("body", {})
            messages = body.get("messages", [])
            if len(messages) < 2:
                continue
            content = str(messages[1].get("content", ""))
            if "<data>" not in content:
                continue
            block = content.split("<data>", 1)[1].split("</data>", 1)[0]
            for item in json.loads(block):
                key = str(item.get("normalized_name", ""))
                if key not in requested:
                    continue
                for field_name in ("hint_display", "hint_raw_text", "display_name", "sample_raw_text"):
                    alias = ingredient_match_key(str(item.get(field_name) or ""))
                    if alias and alias not in requested:
                        aliases.setdefault(alias, key)
    return aliases


def _expand_delegates(
    runner: BatchRunner,
    job_name: str,
    report: ResolveReport,
    still_unmatched: list[dict[str, Any]],
    occurrences: dict[str, int],
) -> list[dict[str, Any]]:
    """대표 -> 변형 매핑을 읽어 결과를 나눠 줍니다. 매핑 파일이 없으면 그대로 둡니다."""
    path = runner.requests_dir / f"{job_name}_delegates.json"
    if not path.exists():
        return still_unmatched

    delegate: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    by_head = {item.normalized_name: item for item in report.llm}
    failed = {item["normalized_name"] for item in still_unmatched}

    for variant, head in sorted(delegate.items()):
        if variant == head or variant in by_head:
            continue
        matched = by_head.get(head)
        if matched is not None:
            report.llm.append(
                MatchResult(
                    normalized_name=variant,
                    ingredient_id=matched.ingredient_id,
                    matched_name=matched.matched_name,
                    method="cluster",
                    confidence=matched.confidence,
                )
            )
        elif head in failed and variant not in failed:
            still_unmatched.append(
                {
                    "normalized_name": variant,
                    "occurrence": occurrences.get(variant, 0),
                    "confidence": 0.0,
                    "reason": f"대표 {head} 가 매칭되지 않음",
                }
            )
    return still_unmatched


def collect(
    job_name: str,
    *,
    report: ResolveReport,
    settings: Settings | None = None,
) -> ResolveReport:
    """LLM 매칭 결과를 읽어 리포트에 반영합니다. 확신도 미만은 미매칭으로 남깁니다."""
    settings = settings or get_settings()
    runner = BatchRunner(STAGE_RESOLVE, settings)
    key_map = json.loads((runner.requests_dir / f"{job_name}_keys.json").read_text(encoding="utf-8"))
    requested = {name for names in key_map.values() for name in names}
    aliases = _build_aliases(runner, job_name, requested)
    occurrences = {item["normalized_name"]: item.get("occurrence", 0) for item in report.unmatched}

    outcome = runner.parse(job_name, IngredientMatchBatch)
    decided: set[str] = set()
    still_unmatched: list[dict[str, Any]] = []

    for _, parsed in outcome.records:
        assert isinstance(parsed, IngredientMatchBatch)
        for match in parsed.matches:
            # LLM 이 공백을 넣거나 빼서 돌려줄 수 있으므로 보낼 때와 같은 키로 되돌립니다.
            # normalized_name 대신 display 표기로 답하는 경우도 있어 별칭까지 봅니다.
            name = ingredient_match_key(match.source_name)
            name = name if name in requested else aliases.get(name, name)
            if name not in requested or name in decided:
                continue
            decided.add(name)
            if match.ingredient_id is not None and match.confidence >= settings.match_min_confidence:
                report.llm.append(
                    MatchResult(
                        normalized_name=name,
                        ingredient_id=match.ingredient_id,
                        matched_name=match.matched_name or "",
                        method="llm",
                        confidence=match.confidence,
                    )
                )
            else:
                still_unmatched.append(
                    {
                        "normalized_name": name,
                        "occurrence": occurrences.get(name, 0),
                        "confidence": match.confidence,
                        "reason": match.reason,
                    }
                )

    for name in sorted(requested - decided):
        still_unmatched.append(
            {"normalized_name": name, "occurrence": occurrences.get(name, 0), "confidence": 0.0, "reason": "응답 누락"}
        )

    # 대표로 물어본 결과를 같은 클러스터의 변형들에 되돌려줍니다.
    # 대표만 붙고 변형이 빠지면 그 레시피들의 재료가 통째로 사라집니다.
    still_unmatched = _expand_delegates(runner, job_name, report, still_unmatched, occurrences)

    report.unmatched = sorted(still_unmatched, key=lambda item: (-item["occurrence"], item["normalized_name"]))
    # collect 를 다시 돌리면 이전 결과가 리스트에 겹쳐 쌓입니다. 저장은 dict 라 값이
    # 정확하지만 화면 숫자가 부풀려져 매칭률을 잘못 읽게 됩니다.
    report.exact = _dedupe(report.exact)
    report.llm = _dedupe(report.llm)
    save_report(report, settings)
    return report


def _dedupe(items: list[MatchResult]) -> list[MatchResult]:
    """이름당 하나만 남깁니다. 먼저 들어온 것을 우선합니다."""
    seen: dict[str, MatchResult] = {}
    for item in items:
        seen.setdefault(item.normalized_name, item)
    return list(seen.values())


async def run_resolve(job_name: str, settings: Settings) -> int:
    """`resolve` 명령 본체. 정확 일치 -> 클러스터 전파 -> 남은 대표만 LLM 요청으로.

    CLI 는 인자만 넘기고 종료코드를 그대로 돌려줍니다. 흐름이 여기 있어야 하는 이유는
    이 함수가 부르는 것이 전부 이 모듈 안에 있기 때문입니다(`collect_names`,
    `exact_match`, `build_requests`, `save_report`). `canonical` 만 옆 모듈입니다.
    """
    records_dir = settings.artifacts_dir / "records"
    names = collect_names(records_dir)
    if not names:
        print("2단계 산출물에 재료명이 없습니다.", file=sys.stderr)
        return 1

    # resolve 는 리포트를 처음부터 다시 만듭니다. 마스터가 바뀌면 옛 LLM 답이
    # 다른 후보 목록을 보고 낸 것이라 그게 맞지만, 조용히 사라지면 매칭률이
    # 갑자기 떨어진 이유를 알 수 없습니다.
    previous = count_llm_matches(settings)
    if previous:
        print(f"주의: 기존 LLM 매칭 {previous}종을 버리고 다시 만듭니다. 배치를 새로 돌려야 합니다.")

    exact, remaining = await exact_match(names, settings)

    # 같은 영문 재료의 한국어 변형끼리 결과를 나눠 씁니다. `eggs` 가 `달걀` 로 붙으면
    # `계란` 도 같이 붙습니다. 번역이 어느 쪽으로 나왔든 매칭이 흔들리지 않게 하려는 것입니다.
    clusters = canonical.build_clusters(records_dir)
    resolved = {item.normalized_name: item.ingredient_id for item in exact}
    gained = canonical.propagate(clusters, resolved)
    by_name = {item.normalized_name: item for item in remaining}
    exact = exact + [
        MatchResult(
            normalized_name=key,
            ingredient_id=ingredient_id,
            matched_name=next(m.matched_name for m in exact if m.ingredient_id == ingredient_id),
            method="cluster",
            confidence=1.0,
        )
        for key, ingredient_id in sorted(gained.items())
    ]
    remaining = [item for item in remaining if item.normalized_name not in gained]

    report = ResolveReport(
        total_names=len(names),
        exact=exact,
        unmatched=[
            {"normalized_name": item.normalized_name, "occurrence": item.occurrence, "confidence": 0.0}
            for item in remaining
        ],
    )
    save_report(report, settings)
    print(f"재료명 {len(names)}종 / 정확 일치 {len(exact) - len(gained)}종 / 클러스터 전파 {len(gained)}종")

    if not remaining:
        print("LLM 매칭이 필요 없습니다. 바로 load 로 넘어가세요.")
        return 0

    # 남은 것 중 같은 클러스터끼리는 대표 하나만 물어봅니다.
    delegate = canonical.representatives(clusters, {item.normalized_name for item in remaining})
    heads = sorted({head for head in delegate.values()})
    ask = [by_name[key] for key in heads if key in by_name]
    print(canonical.render(clusters, unmatched=len(remaining), delegated=len(ask)))

    master = await fetch_master(settings)
    requests, key_map = build_requests(ask, master, settings=settings)
    runner = BatchRunner(STAGE_RESOLVE, settings)
    paths = runner.write_requests(requests, job_name=job_name)
    (runner.requests_dir / f"{job_name}_keys.json").write_text(
        json.dumps(key_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (runner.requests_dir / f"{job_name}_delegates.json").write_text(
        json.dumps(delegate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for path in paths:
        print(f"생성: {path.name} (마스터 {len(master)}행을 후보로 첨부)")
    return 0
