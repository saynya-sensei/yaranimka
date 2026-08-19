"""Память между запусками: за какой день дайджест уже ушёл."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._data: dict = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                # Битый файл не повод падать: хуже дубля дайджеста только
                # бот, который не запускается вообще.
                log.warning("Состояние не прочиталось (%s), начинаем с нуля", exc)

    @property
    def last_digest(self) -> date | None:
        raw = self._data.get("last_digest")
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    def mark_digest(self, day: date) -> None:
        self._data["last_digest"] = day.isoformat()
        self.save()

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Состояние не сохранилось: %s", exc)
