"""3단계: 추출된 재료명을 기존 ingredient 마스터의 id 로 해석.

`ingredient` 는 K-FIND 코드 체계로 큐레이션된 마스터(736행)라 파이프라인이 새 행을
만들지 않습니다. 여기서 하는 일은 2단계가 뽑은 한국어 재료명을 마스터 id 로 잇는 것뿐입니다.

두 단계로 나눕니다.

1. 정확 일치: normalized_name / name / aliases 가 그대로 맞는 것. 공짜라 먼저 씁니다.
2. 나머지: 마스터 목록을 통째로 주고 LLM Batch 가 후보와 확신도를 고릅니다.
   이름을 여러 개 묶어 한 요청에 담아 마스터 목록 토큰을 나눠 씁니다.

확신도가 임계값(`MATCH_MIN_CONFIDENCE`) 미만이면 채택하지 않고 미매칭으로 보고합니다.
마스터를 오염시키느니 사람이 보고 결정하는 편이 낫다는 판단입니다.

## 여섯 조각

    models      NameRequest / MatchResult / ResolveReport
    matching    이름 모으기 + 마스터 조회 + 정확 일치   (LLM 없음)
    requests    LLM 요청 조립                            (프롬프트)
    collect     LLM 응답 수거                            (확신도 판정)
    report      ingredient_matches.json 저장/읽기
    run         명령 본체. 위 다섯을 엮는 유일한 곳

한 파일 569줄이던 것을 나눈 것입니다. 기존 import 경로(`from data_pipeline.stages
import resolve` 뒤 `resolve.<이름>`)를 그대로 쓸 수 있게 여기서 다시 내보냅니다.
"""

# `collect` 는 모듈 이름이자 함수 이름입니다. 아래 한 줄이 서브모듈을 먼저 올리고
# 그 다음 같은 이름을 함수로 덮어쓰기 때문에, 밖에서 보는 `resolve.collect` 는 함수입니다.
# 순서를 바꾸거나 이 줄을 지우면 모듈이 노출되어 호출부가 조용히 터집니다.
from data_pipeline.stages.resolve.collect import collect
from data_pipeline.stages.resolve.matching import (
    collect_names,
    exact_match,
    fetch_master,
    fetch_match_lookup,
)
from data_pipeline.stages.resolve.models import MatchResult, NameRequest, ResolveReport
from data_pipeline.stages.resolve.report import (
    count_llm_matches,
    load_matches,
    load_report,
    matches_path,
    save_report,
)
from data_pipeline.stages.resolve.requests import (
    MATCH_SYSTEM_TEMPLATE,
    MATCH_USER_TEMPLATE,
    build_requests,
    render_master,
)
from data_pipeline.stages.resolve.run import run_resolve

__all__ = [
    "MATCH_SYSTEM_TEMPLATE",
    "MATCH_USER_TEMPLATE",
    "MatchResult",
    "NameRequest",
    "ResolveReport",
    "build_requests",
    "collect",
    "collect_names",
    "count_llm_matches",
    "exact_match",
    "fetch_master",
    "fetch_match_lookup",
    "load_matches",
    "load_report",
    "matches_path",
    "render_master",
    "run_resolve",
    "save_report",
]
