"""``checks.check_reason`` 의 검사 규칙들. 번호는 ``check_reason`` 이 부르는 순서입니다.

- ``factuality``: 1~6 사실성 (다른 카드 재료, 지어낸 재료, 보유 뒤집힘, 상비재료 구매, 조리시간, 영양·건강)
- ``structure``: 7~11 구조 (숫자, 길이, 빈 문구, 형식, 문장 역할)
- ``injection``: 12~14 간접 주입 흔적 (입력에 없는 영문·숫자, 링크, 음식 안전 비방)
- ``text``: 여러 규칙이 함께 쓰는 한국어 재료명 도우미
- ``base``: ``Check`` 결과와 규칙이 받는 ``CheckTarget``

``reason_service`` 는 서빙이 import 하는 운영 코드라 여기서도 상대 import 만 씁니다.
"""
