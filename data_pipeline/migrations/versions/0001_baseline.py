"""baseline: 스키마 v0.1.0 (이미 Neon 에 반영된 상태)

이 리비전은 아무것도 바꾸지 않습니다. 기존 브랜치에는 아래처럼 도장만 찍고 시작합니다.

    uv run alembic stamp 0001

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """스키마 v0.1.0 은 Neon 메인 브랜치에 이미 적용되어 있어 여기서는 아무것도 하지 않습니다."""


def downgrade() -> None:
    """baseline 이라 되돌릴 것이 없습니다."""
