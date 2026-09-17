"""`resolve` 명령 본체.

정확 일치 -> 클러스터 전파 -> 남은 대표만 LLM 요청으로. 이 모듈만 다른 다섯을 전부
알고, 다섯은 서로를 거의 모릅니다.
"""

from __future__ import annotations

import json
import sys

from data_pipeline.batch.client import BatchRunner
from data_pipeline.config import Settings
from data_pipeline.stages import STAGE_RESOLVE, canonical
from data_pipeline.stages.resolve.matching import collect_names, exact_match, fetch_master
from data_pipeline.stages.resolve.models import MatchResult, ResolveReport
from data_pipeline.stages.resolve.report import count_llm_matches, save_report
from data_pipeline.stages.resolve.requests import build_requests


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
    print(f"재료명 {len(names)}종 / 정확 일치 {len(exact) - len(gained)}종 / 클러스터 전파 {len(gained)}종")

    if not remaining:
        save_report(report, settings)
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
    # 이 요청에 후보로 제시한 마스터 id. `collect` 가 이 집합 밖의 id 를 거부합니다.
    # 스키마(`IngredientMatchBatch`)는 아무 정수나 통과시키므로, 제시하지 않은 id 가
    # 확정 매칭으로 저장되면 엉뚱한 재료에 조용히 연결됩니다.
    (runner.requests_dir / f"{job_name}_master_ids.json").write_text(
        json.dumps(sorted(int(row["ingredient_id"]) for row in master), indent=2), encoding="utf-8"
    )

    # **리포트는 여기서 갈아끼웁니다.** 위 산출물 쓰기가 하나라도 실패하면 옛 LLM
    # 결과만 사라지고 새 배치는 없는 상태가 됩니다. 한 트랜잭션이 아니기 때문입니다.
    save_report(report, settings)
    for path in paths:
        print(f"생성: {path.name} (마스터 {len(master)}행을 후보로 첨부)")
    return 0
