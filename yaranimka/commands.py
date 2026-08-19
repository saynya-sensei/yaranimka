"""Разбор команд из сообщений беседы."""

from __future__ import annotations

import re

# Упоминание сообщества приходит разметкой [club123456|Яранимка].
MENTION = re.compile(r"^\s*\[(?:club|public|id)\d+\|[^\]]*\]\s*[,:]?\s*", re.IGNORECASE)

# Явный префикс команды: «/сегодня», «!сегодня».
PREFIX = re.compile(r"^\s*[!/]\s*")

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
    """Текст сообщения → (команда, аргумент). None, если это не команда.

    Команда обязана начинаться с обращения: упоминания сообщества либо «/»
    или «!». Голого слова мало — в живой беседе «завтра приходи» и «аниме
    какое посоветуете» это обычная речь, а не приказ боту. Пока бот не был
    администратором беседы, ВКонтакте показывал ему только сообщения
    с упоминанием, и разница не проявлялась; с правами админа он видит
    всё подряд, и без явного обращения бот отвечал бы на каждый разговор.
    """
    if not text:
        return None

    body = text
    called = False

    if MENTION.match(body):
        body = MENTION.sub("", body, count=1)
        called = True

    if PREFIX.match(body):
        body = PREFIX.sub("", body, count=1)
        called = True

    if not called:
        return None

    head, _, tail = body.strip().partition(" ")
    name = ALIASES.get(head.strip().lower().rstrip("?!,."))
    if not name:
        return None
    return name, tail.strip()
