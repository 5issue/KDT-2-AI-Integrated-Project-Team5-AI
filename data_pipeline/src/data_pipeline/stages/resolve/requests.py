"""LLM Batch 요청 조립.

`matching` 에서 안 붙은 이름만 옵니다. 마스터 목록을 통째로 프롬프트에 넣고 후보와
확신도를 고르게 합니다.

**이름을 여러 개 묶어 한 요청에 담습니다.** 마스터 목록이 요청마다 다시 들어가는데
그게 토큰의 대부분이라, 이름 하나에 요청 하나면 같은 목록을 수백 번 보내게 됩니다.
묶는 개수는 `MATCH_CHUNK_SIZE` 입니다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from data_pipeline.batch.client import build_chat_request
from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import SAFETY_RULES, load_terminology
from data_pipeline.schemas import IngredientMatchBatch
from data_pipeline.stages.resolve.models import NameRequest

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
