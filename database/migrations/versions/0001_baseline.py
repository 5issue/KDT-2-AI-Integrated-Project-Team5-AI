"""baseline - 기존 production 스키마

이 저장소에는 현재 production 스키마를 만든 DDL 이 없다.
Neon `production` 브랜치에 이미 존재하는 15개 테이블이 이 지점이다.

기존 브랜치에서는 아래 명령으로 이 지점을 기록한 뒤 다음 마이그레이션으로 올라간다.

    uv run alembic -c database/alembic.ini stamp 0001_baseline
    uv run alembic -c database/alembic.ini upgrade head

Revision ID: 0001_baseline
Revises:
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """기존 스키마를 그대로 둔다."""


def downgrade() -> None:
    """되돌릴 것이 없다."""
