"""Расписание онгоингов из API Shikimori."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

# Зеркала по порядку. Первое — рабочее, оно же идёт в ссылки.
#
# Обращаться к shikimori.one нельзя: он давно только редиректит на .io, но при
# этом непредсказуемо отвечает 403 — на одни и те же заголовки то пропускает,
# то нет. Каждый запрос через него был лишним походом на ненадёжный хост.
# Оставлен запасным: зеркала у Shikimori периодически меняются местами.
MIRRORS = ("https://shikimori.io", "https://shikimori.one")
API = MIRRORS[0]

# Shikimori требует внятный User-Agent и режет анонимные запросы без него.
HEADERS = {"User-Agent": "yaranimka-vk-bot"}

TIMEOUT = 30.0
ATTEMPTS = 3

# 403 у Shikimori — это защита от ботов, а не «вам сюда нельзя»: другое
# зеркало на тот же запрос отвечает нормально. 429 — просто перебор частоты.
RETRY_CODES = {403, 429}


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
            # Каждая попытка идёт на следующее зеркало: осечка одного хоста
            # лечится не паузой, а другим хостом.
            host = MIRRORS[attempt % len(MIRRORS)]
            try:
                resp = client.get(f"{host}{path}", params=params, headers=HEADERS)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
                log.warning("%s не отозвался (%s/%s): %s", host, attempt + 1, ATTEMPTS, last)
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, list):
                        raise ShikimoriError("Ответ пришёл в неожиданном формате")
                    return data
                last = f"ответил {resp.status_code}"
                # 404 и прочие явные отказы от повторов не поправятся.
                if resp.status_code < 500 and resp.status_code not in RETRY_CODES:
                    break
                log.warning("%s %s (%s/%s)", host, last, attempt + 1, ATTEMPTS)

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


@dataclass(frozen=True)
class News:
    """Заметка с новостного форума Shikimori."""

    title: str
    url: str
    at: datetime

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict) -> "News":
        return cls(str(raw["title"]), str(raw["url"]), datetime.fromisoformat(raw["at"]))


def _news_title(topic: dict) -> str:
    """Заголовок новости, с ромадзи заменённым на русское название.

    Shikimori пишет заголовки вида «Трейлер "Haikyuu!! Bakemono-tachi..."»,
    а рядом в linked лежит русское имя того же тайтла. В беседе на русском
    второе читается несравнимо лучше.
    """
    title = (topic.get("topic_title") or "").strip()
    linked = topic.get("linked") or {}
    romaji = (linked.get("name") or "").strip()
    russian = (linked.get("russian") or "").strip()
    if romaji and russian and romaji in title:
        title = title.replace(romaji, russian)
    return title


def fetch_news(limit: int = 3, client: httpx.Client | None = None, max_age_days: int = 3) -> list[News]:
    """Свежие новости аниме — на русском, с новостного форума Shikimori."""
    if limit <= 0:
        return []

    # Берём с запасом: часть заметок отсеется по возрасту.
    data = _fetch("/api/topics", {"forum": "news", "limit": max(limit * 3, 10)}, client)
    edge = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    out = []
    for topic in data:
        title, topic_id = _news_title(topic), topic.get("id")
        if not title or not topic_id:
            continue
        try:
            at = datetime.fromisoformat(topic.get("created_at") or "")
        except ValueError:
            continue
        if at < edge:
            continue
        out.append(News(title=title, url=f"{API}/forum/news/{topic_id}", at=at))
        if len(out) >= limit:
            break
    return out


# Аниме-сезоны идут кварталами: зима начинается в январе, весна в апреле,
# лето в июле, осень в октябре.
SEASONS = ((1, "winter", "зима"), (4, "spring", "весна"), (7, "summer", "лето"), (10, "fall", "осень"))


def season_of(day: date) -> tuple[str, str, int]:
    """Сезон, которому принадлежит дата: код, русское имя, год."""
    month, name, russian = max((s for s in SEASONS if s[0] <= day.month), key=lambda s: s[0])
    return f"{name}_{day.year}", russian, day.year


def season_start(day: date) -> date:
    """Первый день сезона, в котором находится дата."""
    month = max(s[0] for s in SEASONS if s[0] <= day.month)
    return date(day.year, month, 1)


def next_season_start(day: date) -> date:
    """Первый день следующего сезона."""
    later = [s[0] for s in SEASONS if s[0] > day.month]
    return date(day.year, min(later), 1) if later else date(day.year + 1, 1, 1)


def days_until_next_season(day: date) -> int:
    return (next_season_start(day) - day).days


@dataclass(frozen=True)
class SeasonTitle:
    """Тайтл из сезонной подборки."""

    title: str
    url: str
    kind: str
    starts: date | None

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "kind": self.kind,
                "starts": self.starts.isoformat() if self.starts else None}

    @classmethod
    def from_dict(cls, raw: dict) -> "SeasonTitle":
        starts = raw.get("starts")
        return cls(str(raw["title"]), str(raw["url"]), str(raw.get("kind", "")),
                   date.fromisoformat(starts) if starts else None)


def fetch_season(code: str, limit: int = 5, client: httpx.Client | None = None) -> list[SeasonTitle]:
    """Самые ожидаемые тайтлы сезона.

    Сортировка только по популярности: у ещё не вышедших тайтлов оценка нулевая,
    и ранжировать их по ней бессмысленно. Популярность же показывает именно
    ожидаемость — сколько людей уже добавили тайтл в списки.
    """
    if limit <= 0:
        return []

    data = _fetch(
        "/api/animes",
        {"season": code, "order": "popularity", "limit": max(1, min(limit, 20)), "censored": "true"},
        client,
    )

    out = []
    for anime in data:
        title = (anime.get("russian") or anime.get("name") or "").strip()
        if not title:
            continue
        raw_start = anime.get("aired_on")
        try:
            starts = date.fromisoformat(raw_start) if raw_start else None
        except ValueError:
            starts = None
        out.append(SeasonTitle(
            title=title,
            url=f"{API}{anime.get('url', '')}",
            kind=str(anime.get("kind") or ""),
            starts=starts,
        ))
    return out[:limit]


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
