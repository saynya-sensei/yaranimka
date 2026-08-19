"""Тексты сообщений. ВКонтакте не размечает сообщения, поэтому только текст."""

from __future__ import annotations

from datetime import date, timezone

from .shikimori import Episode

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
• помощь — это сообщение

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


def _line(ep: Episode, tz: timezone, *, links: bool) -> str:
    episode = f"{ep.episode} серия" if ep.episode else "новая серия"
    line = f"• {ep.title} — {episode}, {ep.local_time(tz)}"
    return f"{line}\n  {ep.url}" if links else line


def daily_digest(
    episodes: list[Episode],
    day: date,
    tz: timezone,
    *,
    today: date | None = None,
    links: bool = True,
    max_items: int = 20,
) -> str:
    """Расписание на один день."""
    when = "Сегодня" if day == today else human_date(day, weekday=True).capitalize()

    if not episodes:
        return f"🌸 {when}, {human_date(day)}\n\nНи одной серии — день без онгоингов, отдыхаем."

    shown = episodes[:max_items]
    head = f"🌸 {when}, {human_date(day)} — {len(episodes)} {plural(len(episodes), 'серия', 'серии', 'серий')}"
    body = "\n".join(_line(ep, tz, links=links) for ep in shown)
    text = f"{head}\n\n{body}"

    hidden = len(episodes) - len(shown)
    if hidden > 0:
        text += f"\n\n…и ещё {hidden} {plural(hidden, 'серия', 'серии', 'серий')}"
    return text


def week_digest(
    episodes: list[Episode],
    start: date,
    tz: timezone,
    *,
    days: int = 7,
    links: bool = False,
    max_items: int = 20,
) -> str:
    """Ближайшая неделя, разбитая по дням.

    Ссылки по умолчанию выключены: на семь дней их набирается столько,
    что сообщение превращается в простыню.
    """
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
        lines = "\n".join(_line(ep, tz, links=links) for ep in items)
        hidden = len(by_day[day]) - len(items)
        if hidden > 0:
            lines += f"\n• …и ещё {hidden}"
        blocks.append(f"{human_date(day, weekday=True).capitalize()}\n{lines}")

    return "🌸 Ближайшая неделя\n\n" + "\n\n".join(blocks)


def search_results(query: str, animes: list[dict]) -> str:
    if not animes:
        return f"Ничего не нашла по запросу «{query}»."

    lines = []
    for anime in animes:
        title = (anime.get("russian") or anime.get("name") or "без названия").strip()
        year = (anime.get("aired_on") or "")[:4]
        score = anime.get("score") or "—"
        tail = " · ".join(part for part in (year, f"оценка {score}") if part)
        lines.append(f"• {title} ({tail})\n  https://shikimori.one{anime.get('url', '')}")

    return f"🔎 По запросу «{query}»:\n\n" + "\n".join(lines)
