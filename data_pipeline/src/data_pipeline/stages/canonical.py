"""3단계 앞단: 같은 영문 재료의 한국어 표기를 한 덩어리로 묶습니다.

2단계는 레시피마다 독립적으로 번역합니다. 그래서 같은 영문 재료가 배치마다 다른 한국어로
나옵니다. 실제 1,178건 산출물에서 확인한 것들입니다.

    eggs            -> 계란(147) / 달걀(29)
    vanilla extract -> 바닐라 추출물(87) / 바닐라(50) / 바닐라 익스트랙(39) 외 7개
    baking powder   -> 베이킹파우더(75) / 베이킹 파우더(67)

`eggs` 가 특히 문제입니다. 마스터에 있는 것은 `달걀` 이라 147건이 매칭에 실패합니다.
번역이 어느 쪽으로 나왔느냐에 따라 매칭 성패가 갈립니다.

`ai_context/재료 표준화 및 SKU 범위에 관한 토의.md` 의 권장 순서
(영문 canonical 클러스터링 -> 확정된 canonical 만 번역)를 3단계 앞에 넣은 것입니다.
2단계 산출물에 `name_original`(수량·단위를 뺀 영문 원표기)이 남아 있어 재실행이 필요 없습니다.

하는 일은 두 가지뿐입니다.
- **전파**: 클러스터 안에서 하나라도 마스터에 붙으면 나머지 변형도 같은 id 로 잇습니다.
- **대표 선정**: 아무도 못 붙은 클러스터는 대표 하나만 LLM 에 보냅니다.

못 고치는 것도 분명히 해 둡니다. `white sugar` 와 `brown sugar` 가 **둘 다 `설탕`** 으로
번역된 것은 클러스터가 다르므로 여기서 합쳐지지 않고, 반대로 갈라지지도 않습니다.
번역 품질 문제라 2단계나 사람 검수가 다룰 몫입니다.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from data_pipeline.domain import ingredient_match_key


def _cluster_id(name_original: str) -> str:
    """영문 원표기를 클러스터 키로. 공백과 대소문자만 정규화합니다."""
    return " ".join(name_original.split()).lower()


@dataclass(slots=True)
class Clusters:
    """영문 원표기 -> 한국어 매칭 키들."""

    # 클러스터 id -> {매칭 키: 등장 횟수}
    members: dict[str, Counter[str]] = field(default_factory=dict)

    def variants(self, cluster: str) -> list[str]:
        """등장 횟수 내림차순, 동률이면 이름순. 대표 선정이 실행마다 흔들리면 안 됩니다."""
        counts = self.members.get(cluster, Counter())
        return [key for key, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]

    def clusters_of(self, key: str) -> list[str]:
        """이 매칭 키가 속한 클러스터들. 한 한국어가 여러 영문에 붙을 수 있습니다."""
        return sorted(cluster for cluster, counts in self.members.items() if key in counts)

    def split_count(self) -> int:
        """한국어가 둘 이상으로 갈린 클러스터 수. 리포트용입니다."""
        return sum(1 for counts in self.members.values() if len(counts) > 1)


def build_clusters(records_dir: Path) -> Clusters:
    """2단계 산출물에서 영문 원표기 기준 클러스터를 만듭니다."""
    members: dict[str, Counter[str]] = defaultdict(Counter)

    for path in sorted(records_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            for item in payload.get("ingredients", []):
                original = _cluster_id(str(item.get("name_original") or ""))
                key = ingredient_match_key(str(item.get("normalized_name") or ""))
                if original and key:
                    members[original][key] += 1

    return Clusters(members=dict(members))


def propagate(clusters: Clusters, resolved: dict[str, int]) -> dict[str, int]:
    """클러스터 안에서 하나라도 붙었으면 나머지 변형에도 같은 id 를 줍니다.

    이미 붙어 있는 키는 건드리지 않습니다. 클러스터 안에서 서로 다른 id 로 붙은 변형이
    있으면 **가장 많이 등장한 변형의 id** 를 씁니다. 실행마다 달라지지 않게 하려는 것입니다.

    돌려주는 것은 새로 붙은 것만 담은 사전입니다. 호출한 쪽이 원래 결과와 합칩니다.
    """
    gained: dict[str, int] = {}

    for cluster in sorted(clusters.members):
        variants = clusters.variants(cluster)
        winner = next((key for key in variants if key in resolved), None)
        if winner is None:
            continue
        ingredient_id = resolved[winner]
        for key in variants:
            if key not in resolved and key not in gained:
                gained[key] = ingredient_id

    return gained


def representatives(clusters: Clusters, unmatched: set[str]) -> dict[str, str]:
    """아직 못 붙은 키마다 '이 키를 대신해 물어볼 대표'를 정합니다.

    같은 클러스터의 변형들은 대표 하나만 LLM 에 보내고 결과를 나눠 씁니다.
    `바닐라 추출물` 계열 8개를 8번 물어볼 이유가 없습니다.

    어느 클러스터에도 속하지 않는 키(한국어 원본 레시피 등)는 자기 자신이 대표입니다.
    """
    delegate: dict[str, str] = {}

    for cluster in sorted(clusters.members):
        variants = [key for key in clusters.variants(cluster) if key in unmatched]
        if len(variants) < 2:
            continue
        head = variants[0]
        for key in variants:
            # 이미 다른 클러스터에서 대표가 정해졌으면 그대로 둡니다(결정적 순서라 안정적).
            delegate.setdefault(key, head)

    return {key: delegate.get(key, key) for key in sorted(unmatched)}


def render(clusters: Clusters, unmatched: int, delegated: int) -> str:
    """사람이 읽을 요약."""
    return (
        f"영문 클러스터 {len(clusters.members)}종 / 한국어가 갈린 클러스터 {clusters.split_count()}종\n"
        f"  미매칭 {unmatched}종 -> 대표 {delegated}종만 LLM 에 질의"
    )
