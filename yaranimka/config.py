"""Конфигурация из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone

from dotenv import load_dotenv

# utf-8-sig, а не utf-8: «Блокнот» на Windows сохраняет файл с BOM, и тогда
# первая переменная в .env читается с невидимым префиксом и просто теряется.
load_dotenv(encoding="utf-8-sig")

# ВКонтакте нумерует беседы отдельно от людей: peer_id беседы это её номер
# плюс два миллиарда. В интерфейсе сообщества виден только номер, поэтому
# принимаем оба варианта и приводим к peer_id сами.
CHAT_PEER_OFFSET = 2_000_000_000


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip().replace(",", ".")
    return float(raw) if raw else default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да"}


def chat_peer_id(raw: str | int) -> int:
    """Номер беседы или готовый peer_id — в peer_id. Пусто — ноль."""
    text = str(raw).strip()
    if not text:
        return 0
    value = int(text)
    return value if value >= CHAT_PEER_OFFSET else CHAT_PEER_OFFSET + value


def chat_number(peer_id: int) -> int:
    """peer_id беседы обратно в её номер.

    messages.removeChatUser принимает именно номер: на peer_id он отвечает
    «chat_id should be less than 100000000».
    """
    return peer_id - CHAT_PEER_OFFSET if peer_id >= CHAT_PEER_OFFSET else peer_id


def parse_time(raw: str) -> tuple[int, int]:
    """«10:00» → (10, 0). Пустая строка и мусор поднимают ValueError."""
    hours, _, minutes = raw.strip().partition(":")
    hh, mm = int(hours), int(minutes or 0)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"Некорректное время: {raw!r}")
    return hh, mm


@dataclass
class Config:
    vk_token: str = field(default_factory=lambda: os.getenv("VK_GROUP_TOKEN", "").strip())
    group_id: int = field(default_factory=lambda: _int("VK_GROUP_ID", 0))
    chat_id: str = field(default_factory=lambda: os.getenv("VK_CHAT_ID", "").strip())

    # Во сколько по местному времени уходит дайджест и какой это часовой пояс.
    # Смещение в часах от UTC: 4 — Тбилиси, 3 — Москва.
    daily_at: str = field(default_factory=lambda: os.getenv("YARANIMKA_DAILY_AT", "").strip() or "10:00")
    tz_offset: int = field(default_factory=lambda: _int("YARANIMKA_TZ_OFFSET", 4))

    # Календарь Shikimori отдаёт всё подряд, включая проходные тайтлы.
    # Порог оценки отсекает их, ноль выключает фильтр целиком.
    min_score: float = field(default_factory=lambda: _float("YARANIMKA_MIN_SCORE", 0.0))
    max_items: int = field(default_factory=lambda: _int("YARANIMKA_MAX_ITEMS", 20))

    # Взрослое. В дайджесте по умолчанию скрыто: он прилетает всей беседе
    # без спроса. В ответе на поиск — показано: человек спросил конкретный
    # тайтл, и урезать ему выдачу молча неправильно.
    adult: bool = field(default_factory=lambda: _bool("YARANIMKA_ADULT"))
    adult_search: bool = field(default_factory=lambda: _bool("YARANIMKA_ADULT_SEARCH", True))

    # Новости аниме с форума Shikimori под списком серий. Ноль убирает блок.
    news_items: int = field(default_factory=lambda: _int("YARANIMKA_NEWS", 3))

    # Сезонные напоминания: последняя неделя сезона и старт нового.
    season: bool = field(default_factory=lambda: _bool("YARANIMKA_SEASON", True))
    season_top: int = field(default_factory=lambda: _int("YARANIMKA_SEASON_TOP", 5))

    # Подробный лог: каждое событие Long Poll как есть. Нужен, когда бот
    # молчит и непонятно, не пришло событие или пришло непонятым.
    debug: bool = field(default_factory=lambda: _bool("YARANIMKA_DEBUG"))

    # Ответы на команды в беседе. Ноль делает бота молчаливым: он продолжает
    # присылать дайджест и следить за беседой, но на сообщения не отвечает.
    commands: bool = field(default_factory=lambda: _bool("YARANIMKA_COMMANDS", True))

    # Охрана беседы. ВКонтакте не показывает, что участник вышел, и позволяет
    # ему вернуться самому — бот закрывает и то, и другое.
    leave_notify: bool = field(default_factory=lambda: _bool("YARANIMKA_LEAVE_NOTIFY", True))
    leave_kick: bool = field(default_factory=lambda: _bool("YARANIMKA_LEAVE_KICK", True))

    # Слежение за русскими раздачами вышедших серий. Обе площадки берутся
    # одной лентой на обход, поэтому получасовой интервал остаётся щадящим
    # даже когда в списке два десятка серий.
    watch: bool = field(default_factory=lambda: _bool("YARANIMKA_WATCH", True))
    watch_every: int = field(default_factory=lambda: _int("YARANIMKA_WATCH_EVERY", 30))
    watch_days: int = field(default_factory=lambda: _int("YARANIMKA_WATCH_DAYS", 3))

    # Дайджест уходит раз в сутки, и бот помнит, за какой день уже отправил:
    # без этого перезапуск процесса в 10:05 присылал бы его заново.
    state_path: str = field(default_factory=lambda: os.getenv("YARANIMKA_STATE_PATH", "").strip() or "state.json")
    dry_run: bool = field(default_factory=lambda: _bool("YARANIMKA_DRY_RUN"))

    @property
    def tz(self) -> timezone:
        return timezone(timedelta(hours=self.tz_offset))

    @property
    def peer_id(self) -> int:
        return chat_peer_id(self.chat_id)

    @property
    def daily(self) -> tuple[int, int]:
        return parse_time(self.daily_at)

    def check(self) -> None:
        """Проверяем всё разом, чтобы не падать посреди работы."""
        missing = []
        if not self.vk_token:
            missing.append("VK_GROUP_TOKEN")
        if not self.group_id:
            missing.append("VK_GROUP_ID")
        if not self.chat_id:
            missing.append("VK_CHAT_ID")
        if missing:
            raise SystemExit("Не заданы обязательные переменные: " + ", ".join(missing))

        self.daily  # noqa: B018 — ранняя проверка формата времени
