"""재료 마스터 보강 테스트.

케이스는 실제 「전국통합식품영양성분정보」 구조에서 가져왔습니다.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.batch.raw_source import discover_datasets
from data_pipeline.load import ingredient_master


def write_nutrition(path: Path, records: list[dict[str, object]]) -> None:
    """공공데이터 표준 JSON 형태로 씁니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fields": [{"id": k} for k in records[0]], "records": records}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def record(kind: str, group: str, code: str, name: str, **extra: object) -> dict[str, object]:
    """레코드 한 건."""
    row: dict[str, object] = {
        "데이터구분코드": kind,
        "식품대분류코드": group,
        "대표식품코드": code,
        "대표식품명": name,
    }
    row.update(extra)
    return row


def from_data(rows: list[ingredient_master.MasterRow]) -> list[ingredient_master.MasterRow]:
    """큐레이션 기본 재료를 빼고 데이터에서 나온 것만. 항상 함께 붙어 나옵니다."""
    return [row for row in rows if not row.source_identity_key.startswith("TEAM-BASIC:")]


def test_raw_material_keeps_existing_key_format(tmp_path: Path) -> None:
    """기존 736행과 같은 키라야 새 행을 만들지 않고 별칭만 더합니다."""
    write_nutrition(tmp_path / "r.json", [record("R", "01", "01018", "국수")])
    rows = from_data(ingredient_master.build_master_rows(discover_datasets(tmp_path)))

    assert rows[0].source_identity_key == "K-FIND:01:01018:국수"
    assert rows[0].is_raw_material is True


def test_processed_food_key_is_separated(tmp_path: Path) -> None:
    """대표식품코드가 원재료와 10건 겹칩니다. 접두사로 갈라야 덮어쓰지 않습니다."""
    write_nutrition(tmp_path / "p.json", [record("P", "19", "19801", "버터")])
    rows = from_data(ingredient_master.build_master_rows(discover_datasets(tmp_path)))

    assert rows[0].source_identity_key == "K-FIND-P:19:19801:버터"
    assert rows[0].is_raw_material is False


def test_sub_classifications_become_aliases(tmp_path: Path) -> None:
    """`난백`/`난황` 이 `달걀` 로 붙어야 매칭이 넓어집니다."""
    write_nutrition(
        tmp_path / "r.json",
        [
            record("R", "12", "12001", "달걀", 식품중분류명="유정란", 식품소분류명="난백", 식품세분류명="생것"),
            record("R", "12", "12001", "달걀", 식품중분류명="유정란", 식품소분류명="난황", 식품세분류명="삶은것"),
        ],
    )
    rows = from_data(ingredient_master.build_master_rows(discover_datasets(tmp_path)))

    assert len(rows) == 1, "같은 대표식품은 한 행으로 접혀야 합니다"
    assert rows[0].aliases == ["난백", "난황", "삶은것", "생것", "유정란"]


def test_alias_same_as_name_is_dropped(tmp_path: Path) -> None:
    """대표식품명과 같은 별칭은 의미가 없습니다."""
    write_nutrition(
        tmp_path / "r.json",
        [record("R", "01", "01018", "국수", 식품중분류명="국 수", 식품소분류명="소면")],
    )
    rows = from_data(ingredient_master.build_master_rows(discover_datasets(tmp_path)))

    # `국 수` 는 공백만 다르므로 별칭에서 빠지고 `소면` 만 남습니다.
    assert rows[0].aliases == ["소면"]


def test_dishes_are_not_ingredients(tmp_path: Path) -> None:
    """음식(D)은 완성된 요리라 재료 마스터에 넣지 않습니다."""
    write_nutrition(
        tmp_path / "mix.json",
        [record("R", "01", "01018", "국수"), record("D", "02", "02120", "피자")],
    )
    rows = from_data(ingredient_master.build_master_rows(discover_datasets(tmp_path)))

    assert [row.name for row in rows] == ["국수"]


def test_datasets_without_required_columns_are_skipped(tmp_path: Path) -> None:
    """raw 에는 레시피나 상품 데이터도 섞여 있습니다. 영양성분 형태만 씁니다."""
    write_nutrition(tmp_path / "r.json", [record("R", "01", "01018", "국수")])
    (tmp_path / "other.json").write_text(
        json.dumps([{"recipe_name": "흰밥", "원재료": "멥쌀"}], ensure_ascii=False), encoding="utf-8"
    )
    rows = from_data(ingredient_master.build_master_rows(discover_datasets(tmp_path)))

    assert [row.name for row in rows] == ["국수"]


def test_staging_tuples_match_copy_columns(tmp_path: Path) -> None:
    """COPY 컬럼 순서와 튜플 순서가 어긋나면 값이 엉뚱한 컬럼으로 들어갑니다."""
    write_nutrition(
        tmp_path / "p.json",
        [record("P", "19", "19801", "버터", 식품대분류명="유가공품류", 식품소분류명="가공버터")],
    )
    rows = from_data(ingredient_master.build_master_rows(discover_datasets(tmp_path)))

    # 컬럼: source_identity_key, name, normalized_name, is_raw_material, aliases, is_pantry, category_path
    assert ingredient_master.to_staging_tuples(rows) == [
        ("K-FIND-P:19:19801:버터", "버터", "버터", False, ["가공버터"], False, "가공식품 > 유가공품류")
    ]


def test_curated_basics_are_always_added(tmp_path: Path) -> None:
    """공공데이터에 없는 기본 재료(`물`, `베이킹파우더`)가 빠지면 그 재료줄이 통째로 사라집니다."""
    write_nutrition(tmp_path / "r.json", [record("R", "01", "01018", "국수")])
    rows = ingredient_master.build_master_rows(discover_datasets(tmp_path))

    basics = {row.name: row for row in rows if row.source_identity_key.startswith("TEAM-BASIC:")}
    assert "물" in basics and "베이킹파우더" in basics
    assert basics["물"].is_pantry is True, "상비재료는 부족 재료 계산에서 빠져야 합니다"
    assert "생수" in basics["물"].aliases


def test_pantry_flag_follows_the_curated_list(tmp_path: Path) -> None:
    """상비재료 표시는 공공데이터로 유도할 수 없어 큐레이션 목록이 정본입니다."""
    write_nutrition(
        tmp_path / "p.json",
        [record("P", "13", "13600", "소금"), record("P", "19", "19801", "버터")],
    )
    rows = {row.name: row for row in ingredient_master.build_master_rows(discover_datasets(tmp_path))}

    assert rows["소금"].is_pantry is True
    assert rows["버터"].is_pantry is False
