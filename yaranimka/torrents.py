"""Поиск русских раздач: Nyaa (субтитры и озвучка) и AniLibria (озвучка).

Обе площадки опрашиваются одной лентой на цикл, а не запросом на каждый тайтл:
сопоставление идёт локально. Так бот остаётся вежливым к чужим серверам даже
когда следит за двумя десятками серий сразу.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from xml.etree import ElementTree

import httpx

log = logging.getLogger(__name__)

NYAA = "https://nyaa.si"
ANILIBRIA = "https://anilibria.top"

# Категория 1_4 — «Anime · Non-English-translated», туда попадают русские
# раздачи. Свежих онгоингов там мало: Nyaa у русскоязычных релизеров не в ходу,
# и основной источник — AniLibria. Но лента стоит один запрос, а изредка
# субтитры к новой серии появляются именно здесь.
NYAA_CATEGORY = "1_4"

# Больше пятидесяти релизов за раз AniLibria не отдаёт — вернёт 422.
ANILIBRIA_MAX_LIMIT = 50

HEADERS = {"User-Agent": "yaranimka-vk-bot"}

DUB, SUB, ANY = "dub", "sub", "any"
KIND_LABELS = {DUB: "озвучка", SUB: "субтитры", ANY: "русская раздача"}
KIND_ICONS = {DUB: "🔊", SUB: "📝", ANY: "🇷🇺"}

# Студии озвучки: их имени в названии достаточно, чтобы понять и язык, и вид.
DUB_GROUPS = (
    "anilibria", "aniliberty", "anidub", "anilib", "shiza", "animevost",
    "studio band", "studioband", "jam club", "jamclub", "dream cast",
    "animedia", "kansai", "amazing dubbing", "sovetromantica",
)

DUB_WORDS = ("озвуч", "дубляж", "многоголос", "закадров", "rusdub", "rus dub", "ru dub", "dub")
SUB_WORDS = ("субтитр", "саб", "rusub", "rus sub", "ru sub", "russub", "sub")

# «rus», «russian», «rusdub» — да; «Rurouni Kenshin» — нет. Поэтому не просто
# начало слова, а именно корень rus, плюс отдельно стоящая метка [RU].
RUS_PATTERNS = (
    re.compile(r"\brus\w*", re.IGNORECASE),
    re.compile(r"\bru\b", re.IGNORECASE),
    re.compile(r"\bрус\w*", re.IGNORECASE),
)

CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)

# Римские цифры в названиях сезонов: Youjo Senki II и Youjo Senki 2 — одно и то же.
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8"}

# Слова, которые ничего не говорят о тайтле и только мешают сопоставлению.
NOISE = {"tv", "the", "animation", "season", "series", "part", "cour", "nd", "st", "rd", "th"}

# «4th Season» и «4» — один и тот же сезон.
ORDINAL = re.compile(r"^(\d+)(?:st|nd|rd|th)$")

# Слишком короткое односложное название совпадёт с чем угодно, и такой
# цели мы не верим вовсе.
MIN_SOLO_LEN = 4


@dataclass(frozen=True)
class Release:
    """Найденная раздача."""

    kind: str
    source: str
    name: str
    url: str
    seeders: int | None = None
    size: str | None = None

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, KIND_LABELS[ANY])

    @property
    def icon(self) -> str:
        return KIND_ICONS.get(self.kind, KIND_ICONS[ANY])


# --- сопоставление названий ---


def tokens(text: str) -> set[str]:
    """Название → набор значащих слов, одинаковый для «II» и «2nd Season»."""
    text = text.lower().replace("&", " and ")
    out = set()
    # [^\W_], а не [a-z0-9]: иначе «Lumière» распадается на «lumi» и «re»,
    # и обрывок «re» потом совпадает с чем попало.
    for word in re.findall(r"[^\W_]+", text):
        word = ROMAN.get(word, word)
        ordinal = ORDINAL.match(word)
        if ordinal:
            word = ordinal.group(1)
        if word in NOISE or (len(word) == 1 and not word.isdigit()):
            continue
        out.add(word)
    return out


def variants(names: list[str]) -> list[str]:
    """Добавляет к названиям укороченные версии — до подзаголовка.

    Shikimori хранит полное имя вида «Clevatess II: Majuu no Ou to Itsuwari
    no Yuusha Denshou», а раздачу называют «Clevatess 2». Без короткого
    варианта такой тайтл не совпал бы никогда.

    Режем строго по «двоеточие с пробелом»: в «Re:Zero» двоеточие — часть
    названия, и обрезка до «Re» давала совпадение с любым тайтлом подряд.
    """
    out = []
    for name in names:
        name = (name or "").strip()
        if not name or name in out:
            continue
        out.append(name)
        head = name.split(": ")[0].strip()
        if head and head not in out:
            out.append(head)
    return out


def title_matches(candidate: str, targets: list[str]) -> bool:
    """Есть ли в названии раздачи хоть одно из названий тайтла.

    Сравниваем по словам, а не подстрокой: в имени раздачи вокруг тайтла
    всегда стоят студия, качество и кодек, а порядок слов может отличаться.
    """
    found = tokens(candidate)
    if not found:
        return False

    for target in targets:
        wanted = tokens(target)
        if not wanted:
            continue
        if len(wanted) == 1 and len(next(iter(wanted))) < MIN_SOLO_LEN:
            continue
        hit = len(wanted & found)
        # Длинному названию прощаем одно несовпавшее слово: студии любят
        # сокращать хвосты. Короткому — нет, иначе совпадёт с чем угодно.
        if hit == len(wanted) or (len(wanted) >= 4 and hit >= len(wanted) - 1):
            return True
    return False


# --- номер серии ---

EPISODE_PATTERNS = (
    re.compile(r"\bs\d{1,2}e(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"[-–—]\s*(\d{1,3})(?:v\d)?\s*(?:[\[(]|$|\s)"),
    re.compile(r"\[(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?\]"),
    re.compile(r"\((\d{1,3})\s*[-–—]\s*(\d{1,3})\)"),
    re.compile(r"(\d{1,3})\s*серия", re.IGNORECASE),
    re.compile(r"\bep?\.?\s*(\d{1,3})\b", re.IGNORECASE),
)

# Разрешение, битность и кодек выглядят как числа, но сериями не являются.
NOT_EPISODES = re.compile(r"\b\d{3,4}[pi]\b|\b(?:8|10)\s*bit\b|\bx?26[45]\b|\bhevc\b", re.IGNORECASE)


def covers_episode(name: str, episode: int) -> bool:
    """Относится ли раздача к этой серии — в том числе сборником вида [1-7]."""
    if episode <= 0:
        return False

    cleaned = NOT_EPISODES.sub(" ", name)

    for pattern in EPISODE_PATTERNS:
        for match in pattern.finditer(cleaned):
            start = int(match.group(1))
            tail = match.group(2) if match.lastindex and match.lastindex > 1 else None
            end = int(tail) if tail else start
            if start <= episode <= end:
                return True
    return False


# --- язык и вид перевода ---


def classify(name: str) -> str | None:
    """Русская ли раздача и что в ней — озвучка или субтитры. None — не наша."""
    low = name.lower()

    by_group = any(group in low for group in DUB_GROUPS)
    by_cyrillic = bool(CYRILLIC.search(name))
    by_word = any(pattern.search(name) for pattern in RUS_PATTERNS)

    if not (by_group or by_cyrillic or by_word):
        return None

    # Студия озвучки в названии — самый надёжный признак, он и решает.
    if by_group or any(word in low for word in DUB_WORDS):
        return DUB
    if any(word in low for word in SUB_WORDS):
        return SUB
    return ANY


# --- Nyaa ---


def nyaa_feed(client: httpx.Client, query: str = "") -> list[Release]:
    """Свежие русские раздачи Nyaa одной лентой, без запроса на каждый тайтл."""
    params = {"page": "rss", "c": NYAA_CATEGORY, "f": "0"}
    if query:
        params["q"] = query

    try:
        resp = client.get(NYAA + "/", params=params, headers=HEADERS)
        if resp.status_code != 200:
            log.warning("Nyaa ответил %s", resp.status_code)
            return []
        root = ElementTree.fromstring(resp.content)
    except (httpx.HTTPError, ElementTree.ParseError) as exc:
        log.warning("Nyaa недоступен: %s", exc)
        return []

    return parse_nyaa(root)


def parse_nyaa(root: ElementTree.Element) -> list[Release]:
    ns = {"nyaa": "https://nyaa.si/xmlns/nyaa"}
    out = []
    for item in root.iterfind(".//item"):
        name = (item.findtext("title") or "").strip()
        kind = classify(name) if name else None
        if not kind:
            continue
        seeders = item.findtext("nyaa:seeders", default="", namespaces=ns)
        out.append(Release(
            kind=kind,
            source="Nyaa",
            name=name,
            url=(item.findtext("guid") or "").strip(),
            seeders=int(seeders) if seeders.isdigit() else None,
            size=item.findtext("nyaa:size", default="", namespaces=ns) or None,
        ))
    return out


# --- AniLibria ---


def anilibria_names(release: dict) -> list[str]:
    name = release.get("name") or {}
    return [str(v) for v in (name.get("main"), name.get("english"), name.get("alternative")) if v]


def anilibria_feed(client: httpx.Client, limit: int = ANILIBRIA_MAX_LIMIT) -> list[dict]:
    """Последние обновления AniLibria: релиз обновляется, когда выходит серия.

    Полсотни свежих релизов покрывают примерно двое суток выходов — при
    получасовом обходе новая серия не успевает уйти из ленты.
    """
    try:
        resp = client.get(
            ANILIBRIA + "/api/v1/anime/releases/latest",
            params={"limit": max(1, min(limit, ANILIBRIA_MAX_LIMIT))},
            headers=HEADERS,
        )
        if resp.status_code != 200:
            log.warning("AniLibria ответила %s", resp.status_code)
            return []
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("AniLibria недоступна: %s", exc)
        return []
    return data if isinstance(data, list) else []


def anilibria_torrent(client: httpx.Client, release: dict, episode: int) -> Release:
    """Раздача AniLibria, покрывающая нужную серию. Один запрос на совпадение."""
    names = anilibria_names(release)
    page = ANILIBRIA + "/anime/releases/release/" + str(release.get("alias") or release.get("id"))
    fallback = Release(DUB, "AniLibria", names[0] if names else page, page)

    try:
        resp = client.get(
            ANILIBRIA + "/api/v1/anime/torrents/release/" + str(release["id"]),
            headers=HEADERS,
        )
        torrents = resp.json() if resp.status_code == 200 else []
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("Торренты AniLibria не пришли: %s", exc)
        return fallback

    fitting = [t for t in torrents if covers_episode(str(t.get("label") or ""), episode)]
    if not fitting:
        return fallback

    best = max(fitting, key=lambda t: t.get("seeders") or 0)
    size = best.get("size")
    return Release(
        kind=DUB,
        source="AniLibria",
        name=str(best.get("label") or fallback.name),
        url=page,
        seeders=best.get("seeders"),
        size=f"{size / 1024 ** 3:.1f} ГБ" if isinstance(size, (int, float)) and size else None,
    )


# --- общий поиск ---


def find(
    titles: list[str],
    episode: int,
    *,
    nyaa: list[Release],
    anilibria: list[dict],
    client: httpx.Client | None = None,
) -> list[Release]:
    """Русские раздачи этой серии — по одной лучшей на каждый вид перевода."""
    best: dict[str, Release] = {}
    targets = variants(titles)

    for release in nyaa:
        if not covers_episode(release.name, episode) or not title_matches(release.name, targets):
            continue
        current = best.get(release.kind)
        if current is None or (release.seeders or 0) > (current.seeders or 0):
            best[release.kind] = release

    # AniLibria — всегда озвучка, и спрашивать её незачем, если озвучку
    # уже нашли на Nyaa: это лишний запрос ради второй ссылки на то же самое.
    if DUB not in best and client is not None:
        for item in anilibria:
            if not title_matches(" ".join(anilibria_names(item)), targets):
                continue
            if ((item.get("latest_episode") or {}).get("ordinal") or 0) < episode:
                continue
            best[DUB] = anilibria_torrent(client, item, episode)
            break

    return [best[kind] for kind in (DUB, SUB, ANY) if kind in best]
