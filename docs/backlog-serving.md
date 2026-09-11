# 팀원 전달 사항?

serving(fastapi)에 api_spec.md (검토 후 최종본)기반 ddd기반 서브폴더 구조 생성 및 api 계약에 맞게 구현. 구현 후에는 pytest기반 유닛테스트, 통합테스트로 검증하고 (coverage는 핵심 비즈니스 로직 기준 70~80%이상) -> 팀원에게 전달

```text
# 아래는 예시로 가져온 DDD기반 폴더구조이고, 3-tier 계층보단 세부적으로 구성하는 트렌드를 따른다.
 app/
│   ├── core/                  # 전역 설정, 보안, DB 연결, 공통 미들웨어
│   │   ├── config.py
│   │   ├── database.py
│   │   └── security.py
│   │
│   ├── recommendation/                  # [도메인 예시: Recommendation]
│   │   ├── domain/            # 도메인 모델 (엔티티, 값 객체, 도메인 로직)
│   │   │   ├── model.py
│   │   │   └── repository_interface.py
│   │   │
│   │   ├── application/       # 응용 서비스 (유스케이스, 트랜잭션 조율)
│   │   │   └── service.py
│   │   │
│   │   ├── infrastructure/    # 인프라 계층 (DB 구현체, ORM 모델, 외부 API)
│   │   │   ├── repository.py
│   │   │   └── entities_orm.py
│   │   │
│   │   └── interface/         # 인터페이스 계층 (FastAPI 라우터, DTO/Schema)
│   │       ├── router.py
│   │       └── schemas.py
```

# TODO: 팀원 간 진행사항 공유용 문서, 지속적 수정
