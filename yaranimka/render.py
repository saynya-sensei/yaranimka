"""Тексты сообщений. ВКонтакте не размечает сообщения, поэтому только текст."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timezone

from . import shikimori
from .shikimori import Episode, News, SeasonTitle
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

• /сегодня — что выходит сегодня
• /завтра — что выходит завтра
• /неделя — ближайшие серии на семь дней
• /аниме <название> — найти тайтл на Shikimori
• /раздачи — доступные торренты к вышедшим сериям
• /помощь — это сообщение

Второй раз за день писать не буду: как выйдет серия, ищу русскую озвучку и субтитры и дописываю ссылки прямо в утреннее сообщение.

Обращайтесь через «/», «!» или упоминание — иначе я не отзовусь и не буду лезть в ваш разговор."""


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


def season_farewell(days_left: int) -> str:
    """Напоминание в последнюю неделю сезона."""
    left = "неделя" if days_left == 7 else f"{days_left} {plural(days_left, 'день', 'дня', 'дней')}"
    verb = "осталась" if days_left == 7 else plural(days_left, "остался", "осталось", "осталось")
    return (f"⏳ У этого аниме-сезона {verb} {left} — проверьте торренты "
            f"на наличие завершившихся тайтлов!")


def season_opening(titles: Sequence[SeasonTitle], name: str, year: int) -> str:
    """Приветствие нового сезона с самыми ожидаемыми тайтлами."""
    head = f"🎬 Стартовал новый аниме-сезон — {name} {year}!"
    if not titles:
        return head

    lines = []
    for item in titles:
        when = f", с {item.starts.day} {MONTHS[item.starts.month - 1]}" if item.starts else ""
        lines.append(f"• {item.title}{when}\n  {item.url}")
    return f"{head}\n\n" + "\n".join(lines)


def mention(user_id: int, name: str) -> str:
    """Ссылка на профиль. Уведомления у сообщения выключены, так что не пингует."""
    return f"[id{user_id}|{name or 'участник'}]"


def left_notice(user_id: int, name: str, *, kicked: bool) -> str:
    """Кто вышел из беседы. ВКонтакте об этом молчит, поэтому говорим мы."""
    who = mention(user_id, name)
    if kicked:
        return f"🚪 {who} вышел из беседы. Вернуться самостоятельно уже не получится — нужно приглашение."
    return f"🚪 {who} вышел из беседы."


def returned_notice(user_id: int, name: str) -> str:
    """Вышедший вернулся сам — и был выставлен обратно."""
    return f"🚪 {mention(user_id, name)} вернулся в беседу сам и был исключён. Пригласите его заново, если это ошибка."


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
    season: str = "",
    hour: int | None = None,
    updated: str | None = None,
    limit: int = MAX_LEN,
) -> str:
    """Расписание на один день — ровно одним сообщением.

    Единственная ссылка в строке — на раздачу, и появляется она только когда
    раздача есть. Сообщение одно и правится на месте, резать его на части
    нельзя, поэтому при переполнении жертвуем по очереди: сначала новости,
    потом сезонный блок, и только в последнюю очередь режем само расписание.
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

    def build(count: int, with_news: bool, with_season: bool) -> str:
        text = head
        if episodes:
            body = "\n".join(_line(ep, tz, found.get(ep.key, ())) for ep in episodes[:count])
            text += "\n\n" + body
            hidden = len(episodes) - count
            if hidden > 0:
                text += f"\n\n…и ещё {hidden} {plural(hidden, 'серия', 'серии', 'серий')}"
        if with_season and season:
            text += f"\n\n{season}"
        if with_news and news:
            notes = "\n".join(f"• {item.title}\n  {item.url}" for item in news)
            text += f"\n\n📰 Что нового в аниме\n\n{notes}"
        if updated:
            text += f"\n\nОбновлено в {updated}"
        return text

    count = max(1, min(max_items, len(episodes))) if episodes else 0

    # Порядок жертв: новости, затем сезонный блок, затем сам список.
    for with_news, with_season in ((True, True), (False, True), (False, False)):
        text = build(count, with_news, with_season)
        if len(text) <= limit:
            return text

    while len(text) > limit and count > 1:
        count -= 1
        text = build(count, False, False)
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


def _release_line(release: Release) -> str:
    facts = [release.source]
    if release.seeders is not None:
        facts.append(f"{release.seeders} {plural(release.seeders, 'сид', 'сида', 'сидов')}")
    if release.size:
        facts.append(release.size)
    return f"  {release.icon} {release.label} · {' · '.join(facts)} ({release.url})"


def previous_release(item: Watched, history: Sequence[Watched]) -> Watched | None:
    """Самая свежая серия того же тайтла, к которой раздача уже есть."""
    earlier = [
        other for other in history
        if other.anime_id == item.anime_id and other.episode < item.episode and other.releases
    ]
    return max(earlier, key=lambda other: other.episode) if earlier else None


def watch_status(watching: list[Watched], history: Sequence[Watched] = ()) -> str:
    """Ответ на команду: какие торренты доступны к сегодняшним сериям.

    Время выхода серии тут не печатается: оно уже есть в дневном списке,
    а здесь спрашивают про торренты, и в скобках полезнее ссылка на них.

    Если к свежей серии раздачи ещё нет, показываем предыдущую: ждать
    вечера ради ссылки не надо, а посмотреть с прошлой серии — можно
    прямо сейчас. `history` для этого и нужна: там записи прошлых дней,
    которые в сам список не попадают.
    """
    if not watching:
        return "📦 Сейчас ничего не отслеживаю — свежих серий в списке нет."

    lines = []
    for item in watching:
        lines.append(f"• {item.title}, {item.episode} серия")

        if item.releases:
            lines.extend(_release_line(release) for release in item.releases)
            continue

        earlier = previous_release(item, history)
        if earlier:
            lines.append(f"  свежей пока нет, последняя доступная — {earlier.episode} серия:")
            lines.extend(_release_line(release) for release in earlier.releases)
        else:
            lines.append("  пока ничего")

    return "📦 Доступные торренты\n\n" + "\n".join(lines)


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
