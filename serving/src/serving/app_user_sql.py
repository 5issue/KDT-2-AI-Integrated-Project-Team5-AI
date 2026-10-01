"""사용자 id 를 `app_user` 에 등록하는 SQL. 사용자 정보의 정본은 BE user DB 입니다.

사용자 id 는 JWT 의 `sub`, 곧 BE `users.id` 입니다(`docs/api/be-sync.md` 2절). 우리 DB 의 `app_user` 는
id 와 등록 시각만 가진 표이고, user_fridge·user_recipe_favorite·user_recipe_view 가 FK 로 가리킵니다.
BE 사용자를 미리 옮겨 두지 않으므로 쓰기 요청이 온 사용자를 쓰기 직전에 등록합니다.

등록하지 않으면 BE 사용자의 첫 냉장고 담기는 FK 위반(500)이 되고, 찜·최근 본 기록은
"사용자를 찾을 수 없습니다"(404)가 됩니다. 사용자 id 는 JWT 서명 검증(또는 로컬의 X-User-Id)을 거친
값만 옵니다.
"""

from __future__ import annotations

# 이미 있으면 아무것도 하지 않습니다. 등록 시각은 표 기본값(now())입니다.
ENSURE_USER = "INSERT INTO app_user (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING"
