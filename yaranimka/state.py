"""Память между запусками: отправленный дайджест и его строки.

Дневной список хранится целиком, а не пересобирается из календаря: как только
серия выходит, Shikimori переводит тайтл на следующую и сегодняшний список
тает прямо на глазах. Редактировать сообщение было бы уже нечем.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .shikimori import Episode, News
from .torrents import Release

log = logging.getLogger(__name__)


@dataclass
class Watched:
    """Строка дневного списка: серия и найденные к ней раздачи."""

    key: str
    day: date
    title: str
    romaji: str
    episode: int
    aired: datetime
    url: str
    releases: list[Release]

    @property
    def titles(self) -> list[str]:
        return [name for name in (self.romaji, self.title) if name]

    @property
    def kinds(self) -> set[str]:
        return {release.kind for release in self.releases}

    def to_episode(self) -> Episode:
        """Обратно в Episode — чтобы дайджест собирался одним и тем же кодом."""
        anime_id = int(self.key.split(":")[0]) if self.key.split(":")[0].isdigit() else 0
        return Episode(anime_id, self.title, self.episode, self.aired, 0.0, self.url, self.romaji)

    def as_dict(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "title": self.title,
            "romaji": self.romaji,
            "episode": self.episode,
            "aired": self.aired.isoformat(),
            "url": self.url,
            "releases": [release.as_dict() for release in self.releases],
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

    @property
    def digest_message(self) -> int | None:
        """Идентификатор отправленного сегодня сообщения — его и правим."""
        digest = self._data.get("digest") or {}
        if digest.get("day") != (self.last_digest.isoformat() if self.last_digest else None):
            return None
        message_id = digest.get("message_id")
        return int(message_id) if message_id else None

    @property
    def digest_news(self) -> list[News]:
        """Новости того же снимка: при правке сообщения они меняться не должны."""
        digest = self._data.get("digest") or {}
        if digest.get("day") != (self.last_digest.isoformat() if self.last_digest else None):
            return []
        out = []
        for raw in digest.get("news", []):
            try:
                out.append(News.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def mark_digest(self, day: date, message_id: int | None, news: list[News] | None = None) -> None:
        self._data["last_digest"] = day.isoformat()
        self._data["digest"] = {
            "day": day.isoformat(),
            "message_id": message_id,
            "news": [item.as_dict() for item in (news or [])],
        }

    # --- строки дневного списка ---

    def watching(self, day: date | None = None) -> list[Watched]:
        out = []
        for key, raw in dict(self._data["watch"]).items():
            try:
                out.append(Watched(
                    key=key,
                    day=date.fromisoformat(raw["day"]),
                    title=raw["title"],
                    romaji=raw.get("romaji", ""),
                    episode=int(raw["episode"]),
                    aired=datetime.fromisoformat(raw["aired"]),
                    url=raw.get("url", ""),
                    releases=[Release.from_dict(item) for item in raw.get("releases", [])],
                ))
            except (KeyError, TypeError, ValueError):
                log.warning("Выбрасываю непонятную запись слежения: %s", key)
                self._data["watch"].pop(key, None)

        if day is not None:
            out = [item for item in out if item.day == day]
        out.sort(key=lambda item: item.aired)
        return out

    def watch(self, episode: Episode, day: date) -> bool:
        """Ставит серию в дневной список. False — такая уже есть."""
        if episode.key in self._data["watch"]:
            return False
        self._data["watch"][episode.key] = Watched(
            key=episode.key, day=day, title=episode.title, romaji=episode.romaji,
            episode=episode.episode, aired=episode.at, url=episode.url, releases=[],
        ).as_dict()
        return True

    def add_release(self, key: str, release: Release) -> None:
        entry = self._data["watch"].get(key)
        if entry is None:
            return
        found = entry.setdefault("releases", [])
        if all(item.get("kind") != release.kind for item in found):
            found.append(release.as_dict())

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
