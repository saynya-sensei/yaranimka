"""Память между запусками: что уже отправлено и за какими сериями следим."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Watched:
    """Серия, для которой ещё ждём появления русской раздачи."""

    key: str
    title: str
    romaji: str
    episode: int
    aired: datetime
    found: list[str]

    @property
    def titles(self) -> list[str]:
        return [name for name in (self.romaji, self.title) if name]

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "romaji": self.romaji,
            "episode": self.episode,
            "aired": self.aired.isoformat(),
            "found": self.found,
        }


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
        if not isinstance(self._data.get("watch"), dict):
            self._data["watch"] = {}

    # --- дайджест ---

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

    # --- слежение за раздачами ---

    def watching(self) -> list[Watched]:
        out = []
        for key, raw in dict(self._data["watch"]).items():
            try:
                out.append(Watched(
                    key=key,
                    title=raw["title"],
                    romaji=raw.get("romaji", ""),
                    episode=int(raw["episode"]),
                    aired=datetime.fromisoformat(raw["aired"]),
                    found=list(raw.get("found", [])),
                ))
            except (KeyError, TypeError, ValueError):
                log.warning("Выбрасываю непонятную запись слежения: %s", key)
                self._data["watch"].pop(key, None)
        out.sort(key=lambda w: w.aired)
        return out

    def watch(self, key: str, *, title: str, romaji: str, episode: int, aired: datetime) -> bool:
        """Ставит серию на слежение. False — такая уже в списке."""
        if key in self._data["watch"]:
            return False
        self._data["watch"][key] = Watched(key, title, romaji, episode, aired, []).as_dict()
        return True

    def mark_found(self, key: str, kind: str) -> None:
        entry = self._data["watch"].get(key)
        if entry is not None and kind not in entry.setdefault("found", []):
            entry["found"].append(kind)

    def unwatch(self, key: str) -> None:
        self._data["watch"].pop(key, None)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Состояние не сохранилось: %s", exc)
