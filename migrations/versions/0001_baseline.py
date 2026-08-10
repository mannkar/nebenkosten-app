"""baseline: aktueller Modellstand vor Einfuehrung von Alembic

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-10

Diese Migration bildet den Datenbankstand ab, wie er bisher direkt per
Base.metadata.create_all() beim App-Start erzeugt wurde. Sie nutzt bewusst
die SQLAlchemy-Modelle selbst (statt einzeln von Hand abgetippter
Tabellen-Definitionen), damit sie garantiert exakt dem in app/models.py
definierten Schema entspricht.

Fuer bereits bestehende Datenbanken (lokale Entwicklung, Produktiv-NAS) wird
diese Migration NICHT ausgefuehrt, sondern der aktuelle Stand einmalig per

    alembic stamp head

als bereits erledigt markiert - die Tabellen existieren dort ja schon exakt
in dieser Form. upgrade() legt die Tabellen nur dann tatsaechlich an, wenn
eine komplett neue/leere Datenbank migriert wird (z. B. eine frische
Testumgebung).
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    from app import models  # noqa: F401  (registriert alle Modelle an Base.metadata)

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=op.get_bind())
