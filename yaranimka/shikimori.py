"""Расписание онгоингов из API Shikimori."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

log = logging.getLogger(__name__)

API = "https://shikimori.one"

# Shikimori требует внятный User-Agent и режет анонимные запросы без него.
HEADERS = {"User-Agent": "yaranimka-vk-bot"}

TIMEOUT = 30.0
ATTEMPTS = 3


class ShikimoriError(RuntimeError):
    pass


@dataclass(frozen=True)
class Episode:
    """Ближайшая серия одного онгоинга."""

    anime_id: int
    title: str
    episode: int
    at: datetime
    score: float
    url: str
    romaji: str = ""

    @property
    def titles(self) -> list[str]:
        """Названия для поиска раздач: их именуют ромадзи, реже — русским."""
        return [name for name in (self.romaji, self.title) if name]

    @property
    def key(self) -> str:
        """Идентификатор серии: им бот помнит, о чём уже написал в беседу."""
        return f"{self.anime_id}:{self.episode}"

    def local_date(self, tz: timezone) -> date:
        return self.at.astimezone(tz).date()

    def local_time(self, tz: timezone) -> str:
        return self.at.astimezone(tz).strftime("%H:%M")


def _title(anime: dict) -> str:
    """Русское название, если Shikimori его знает, иначе ромадзи."""
    return (anime.get("russian") or "").strip() or (anime.get("name") or "").strip() or "без названия"


def _score(anime: dict) -> float:
    try:
        return float(anime.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse(entry: dict) -> Episode | None:
    anime = entry.get("anime") or {}
    raw_at = entry.get("next_episode_at")
    anime_id = anime.get("id")
    if not raw_at or not anime_id:
        return None

    try:
        at = datetime.fromisoformat(raw_at)
    except ValueError:
        log.warning("Не разобрал дату серии: %r", raw_at)
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)

    return Episode(
        anime_id=int(anime_id),
        title=_title(anime),
        episode=int(entry.get("next_episode") or 0),
        at=at,
        score=_score(anime),
        url=f"{API}{anime.get('url', f'/animes/{anime_id}')}",
        romaji=(anime.get("name") or "").strip(),
    )


def _fetch(path: str, params: dict, client: httpx.Client | None) -> list:
    """Запрос к Shikimori с повторами.

    Сеть до Shikimori заметно капризнее, чем до остальных источников:
    зеркала отвечают то за секунду, то не отвечают вовсе. Одна такая
    осечка не должна ронять весь запуск, поэтому пробуем трижды.
    """
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
    last = "неизвестно почему"

    try:
        for attempt in range(ATTEMPTS):
            try:
                resp = client.get(f"{API}{path}", params=params, headers=HEADERS)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
                log.warning("Shikimori не отозвался (%s/%s): %s", attempt + 1, ATTEMPTS, last)
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, list):
                        raise ShikimoriError("Ответ пришёл в неожиданном формате")
                    return data
                last = f"ответил {resp.status_code}"
                # 4xx повторять бессмысленно: ответ не изменится.
                if resp.status_code < 500:
                    break
                log.warning("Shikimori %s (%s/%s)", last, attempt + 1, ATTEMPTS)

            if attempt + 1 < ATTEMPTS:
                time.sleep(2**attempt)
    finally:
        if own:
            client.close()

    raise ShikimoriError(f"Shikimori недоступен: {last}")


def fetch_calendar(client: httpx.Client | None = None) -> list[Episode]:
    """Календарь ближайших серий: по одной ближайшей на каждый онгоинг.

    Именно так устроен эндпоинт — вторую и третью серию вперёд он не отдаёт,
    поэтому недельная сводка показывает только по одному эпизоду на тайтл.
    """
    data = _fetch("/api/calendar", {"censored": "true"}, client)
    episodes = [ep for ep in (_parse(item) for item in data) if ep]
    episodes.sort(key=lambda ep: (ep.at, ep.title))
    return episodes


def on_day(episodes: list[Episode], day: date, tz: timezone, *, min_score: float = 0.0) -> list[Episode]:
    """Серии, выходящие в указанный день по местному времени."""
    return [ep for ep in episodes if ep.local_date(tz) == day and ep.score >= min_score]


def search(query: str, limit: int = 5, client: httpx.Client | None = None) -> list[dict]:
    """Поиск аниме по названию — для команды в беседе."""
    return _fetch(
        "/api/animes",
        {"search": query, "limit": max(1, min(limit, 10)), "censored": "true"},
        client,
    )
