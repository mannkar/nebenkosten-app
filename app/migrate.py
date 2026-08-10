"""Wendet beim App-Start ausstehende Alembic-Migrationen automatisch an.

Ersetzt das bisherige Base.metadata.create_all() beim Start: statt bei jeder
Modelländerung die Datenbank löschen und neu anlegen zu müssen, wird das
Schema jetzt inkrementell per Alembic-Migration nachgezogen - bestehende
Daten bleiben erhalten.

Vor jeder Migration wird die SQLite-Datei sicherheitshalber als Kopie neben
das Original gelegt (nebenkosten.db -> nebenkosten.db.backup-YYYYMMDD-HHMMSS),
damit ein fehlgeschlagenes Upgrade nicht zu Datenverlust führt. Alte Backups
werden nicht automatisch gelöscht - bei Bedarf im data/-Ordner manuell
aufräumen.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime

from alembic import command
from alembic.config import Config

from .database import DB_PATH

_PROJEKT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ALEMBIC_INI = os.path.join(_PROJEKT_ROOT, "alembic.ini")


def _backup_datenbank() -> None:
    if not os.path.exists(DB_PATH):
        return  # brandneue Datenbank - es gibt noch nichts zu sichern
    zeitstempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    ziel = f"{DB_PATH}.backup-{zeitstempel}"
    shutil.copy2(DB_PATH, ziel)


def migrieren() -> None:
    """Sichert die DB und wendet alle ausstehenden Migrationen an (No-Op,
    falls bereits alles auf dem neuesten Stand ist)."""
    _backup_datenbank()
    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("script_location", os.path.join(_PROJEKT_ROOT, "migrations"))
    command.upgrade(cfg, "head")
