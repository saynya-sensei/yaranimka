"""Тексты сообщений. ВКонтакте не размечает сообщения, поэтому только текст."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timezone

from . import shikimori
from .shikimori import Episode, News
from .state import Watched
from .torrents import Release
from .vk import MAX_LEN

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

HELP = """🌸 Я подсказываю, что из онгоингов выходит сегодня.

Раз в сутки сама присылаю расписание на день. А ещё понимаю команды:

• сегодня — что выходит сегодня
• завтра — что выходит завтра
• неделя — ближайшие серии на семь дней
• аниме <название> — найти тайтл на Shikimori
• раздачи — за какими сериями слежу и что уже нашлось
• помощь — это сообщение

Второй раз за день писать не буду: как выйдет серия, ищу русскую озвучку и субтитры и дописываю ссылки прямо в утреннее сообщение.

Команду можно писать с восклицательным знаком, слэшем или через упоминание — всё равно пойму."""


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование: 1 серия, 2 серии, 5 серий."""
    if 11 <= n % 100 <= 14:
        return many
    tail = n % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def human_date(day: date, *, weekday: bool = False) -> str:
    text = f"{day.day} {MONTHS[day.month - 1]}"
    return f"{WEEKDAYS[day.weekday()]}, {text}" if weekday else text


def greeting(hour: int) -> str:
    """Приветствие по времени суток — дайджест могут запустить когда угодно."""
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    if 18 <= hour < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def _line(ep: Episode, tz: timezone, releases: Sequence[Release] = ()) -> str:
    episode = f"{ep.episode} серия" if ep.episode else "новая серия"
    parts = [f"• {ep.title} — {episode}, {ep.local_time(tz)}"]
    for release in releases:
        parts.append(f"  {release.icon} {release.label}: {release.url}")
    return "\n".join(parts)


def daily_digest(
    episodes: list[Episode],
    day: date,
    tz: timezone,
    *,
    today: date | None = None,
    max_items: int = 20,
    releases: dict[str, Sequence[Release]] | None = None,
    news: Sequence[News] = (),
    hour: int | None = None,
    updated: str | None = None,
    limit: int = MAX_LEN,
) -> str:
    """Расписание на один день — ровно одним сообщением.

    Единственная ссылка в строке — на раздачу, и появляется она только когда
    раздача есть. Сообщение одно и правится на месте, резать его на части
    нельзя, поэтому при переполнении сначала уходят новости, а потом
    сокращается список: расписание тут главное, новости — довесок.
    """
    hello = f"{greeting(hour if hour is not None else 12)}, любимые накамычи! 🌸"
    when = human_date(day, weekday=True).capitalize()
    count_word = f"{len(episodes)} {plural(len(episodes), 'серия', 'серии', 'серий')} онгоингов"
    today_word = "сегодня " if day == today else ""

    if not episodes:
        head = f"{hello}\n\n{when} — сегодня онгоинги отдыхают, ни одной серии."
    else:
        head = f"{hello}\n\n{when} — {today_word}выходит {count_word}"

    found = releases or {}

    def build(count: int, with_news: bool) -> str:
        text = head
        if episodes:
            body = "\n".join(_line(ep, tz, found.get(ep.key, ())) for ep in episodes[:count])
            text += "\n\n" + body
            hidden = len(episodes) - count
            if hidden > 0:
                text += f"\n\n…и ещё {hidden} {plural(hidden, 'серия', 'серии', 'серий')}"
        if with_news and news:
            notes = "\n".join(f"• {item.title}\n  {item.url}" for item in news)
            text += f"\n\n📰 Что нового в аниме\n\n{notes}"
        if updated:
            text += f"\n\nОбновлено в {updated}"
        return text

    count = max(1, min(max_items, len(episodes))) if episodes else 0

    text = build(count, True)
    if len(text) <= limit:
        return text

    text = build(count, False)
    while len(text) > limit and count > 1:
        count -= 1
        text = build(count, False)
    return text[:limit]


def week_digest(
    episodes: list[Episode],
    start: date,
    tz: timezone,
    *,
    days: int = 7,
    max_items: int = 20,
) -> str:
    """Ближайшая неделя, разбитая по дням."""
    by_day: dict[date, list[Episode]] = {}
    for ep in episodes:
        day = ep.local_date(tz)
        if start <= day < date.fromordinal(start.toordinal() + days):
            by_day.setdefault(day, []).append(ep)

    if not by_day:
        return "🌸 На ближайшую неделю расписание пустое."

    blocks = []
    for day in sorted(by_day):
        items = by_day[day][:max_items]
        lines = "\n".join(_line(ep, tz) for ep in items)
        hidden = len(by_day[day]) - len(items)
        if hidden > 0:
            lines += f"\n• …и ещё {hidden}"
        blocks.append(f"{human_date(day, weekday=True).capitalize()}\n{lines}")

    return "🌸 Ближайшая неделя\n\n" + "\n\n".join(blocks)


def watch_status(watching: list[Watched], tz: timezone) -> str:
    """Ответ на команду: что бот отслеживает и с какими подробностями нашёл.

    В сам дайджест такие детали не помещаются — там только ссылка, — а тут
    места хватает на источник, число сидов и размер.
    """
    if not watching:
        return "📦 Сейчас ничего не отслеживаю — свежих серий в списке нет."

    lines = []
    for item in watching:
        when = item.aired.astimezone(tz).strftime("%d.%m %H:%M")
        lines.append(f"• {item.title}, {item.episode} серия ({when})")
        if not item.releases:
            lines.append("  пока ничего")
        for release in item.releases:
            facts = [release.source]
            if release.seeders is not None:
                facts.append(f"{release.seeders} {plural(release.seeders, 'сид', 'сида', 'сидов')}")
            if release.size:
                facts.append(release.size)
            lines.append(f"  {release.icon} {release.label} · {' · '.join(facts)}")
            lines.append(f"  {release.url}")

    return "📦 Русские раздачи\n\n" + "\n".join(lines)


def search_results(query: str, animes: list[dict]) -> str:
    if not animes:
        return f"Ничего не нашла по запросу «{query}»."

    lines = []
    for anime in animes:
        title = (anime.get("russian") or anime.get("name") or "без названия").strip()
        year = (anime.get("aired_on") or "")[:4]
        score = anime.get("score") or "—"
        tail = " · ".join(part for part in (year, f"оценка {score}") if part)
        lines.append(f"• {title} ({tail})\n  {shikimori.API}{anime.get('url', '')}")

    return f"🔎 По запросу «{query}»:\n\n" + "\n".join(lines)
