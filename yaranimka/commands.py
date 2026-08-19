"""Разбор команд из сообщений беседы."""

from __future__ import annotations

import re

# Упоминание сообщества приходит разметкой [club123456|Яранимка]. Пока бот не
# админ беседы, ВКонтакте отдаёт ему только такие сообщения — значит, префикс
# надо снимать перед разбором, иначе ни одна команда не совпадёт.
MENTION = re.compile(r"^\s*\[(?:club|public|id)\d+\|[^\]]*\]\s*[,:]?\s*", re.IGNORECASE)

ALIASES = {
    "сегодня": "today",
    "today": "today",
    "завтра": "tomorrow",
    "неделя": "week",
    "неделю": "week",
    "аниме": "search",
    "найди": "search",
    "раздачи": "releases",
    "раздача": "releases",
    "торренты": "releases",
    "помощь": "help",
    "помоги": "help",
    "начать": "help",
    "старт": "help",
    "start": "help",
    "help": "help",
}


def parse(text: str) -> tuple[str, str] | None:
    """Текст сообщения → (команда, аргумент). None, если это не команда."""
    if not text:
        return None

    cleaned = MENTION.sub("", text).strip().lstrip("!/.").strip()
    if not cleaned:
        return None

    head, _, tail = cleaned.partition(" ")
    name = ALIASES.get(head.strip().lower().rstrip("?!,."))
    if not name:
        return None
    return name, tail.strip()
