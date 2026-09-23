"""baseline - 기존 production 스키마

Neon `production` 브랜치에 2026-09-18 시점 존재하던 13개 테이블이 이 지점이다.
그 DDL 은 `database/schema/0001_baseline.sql` 에 있고, 빈 DB 에서만 실행한다.

기존 브랜치에서는 아래 명령으로 이 지점을 기록한 뒤 다음 마이그레이션으로 올라간다.

    uv run alembic -c database/alembic.ini stamp 0001_baseline
    uv run alembic -c database/alembic.ini upgrade head

빈 DB(로컬, CI)는 stamp 없이 `upgrade head` 한 번이면 된다.

Revision ID: 0001_baseline
Revises:
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_SQL = Path(__file__).resolve().parents[2] / "schema" / "0001_baseline.sql"


def upgrade() -> None:
    """테이블이 하나도 없을 때만 baseline DDL 을 만든다. 기존 DB 는 그대로 둔다."""
    if sa.inspect(op.get_bind()).has_table("recipe"):
        return
    op.execute(BASELINE_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    """표를 지우지 않는다.

    기존 DB 에서는 이 revision 이 표를 만들지 않았고, 빈 DB 에서도 base 는 Production 의 출발점이다.
    base 까지 되돌리면 13개 표가 그대로 남는다.
    """
