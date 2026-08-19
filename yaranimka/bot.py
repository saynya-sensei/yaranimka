"""Бот: ежедневный дайджест онгоингов и ответы на команды в беседе."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import httpx

from . import render
from .commands import parse
from .config import Config
from .shikimori import Episode, HEADERS, ShikimoriError, fetch_calendar, on_day, search
from .state import State
from .vk import LongPoll, VKClient

log = logging.getLogger(__name__)

BROKEN = "Расписание сейчас не отвечает, попробуйте позже."


class Bot:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._vk = VKClient(cfg.vk_token, dry_run=cfg.dry_run)
        self._web = httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True)
        self._state = State(cfg.state_path)

    def close(self) -> None:
        self._vk.close()
        self._web.close()

    def __enter__(self) -> "Bot":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- расписание ---

    def _calendar(self) -> list[Episode]:
        return fetch_calendar(self._web)

    def now(self) -> datetime:
        return datetime.now(self._cfg.tz)

    def digest(self, day: date | None = None) -> str:
        cfg = self._cfg
        today = self.now().date()
        day = day or today
        episodes = on_day(self._calendar(), day, cfg.tz, min_score=cfg.min_score)
        return render.daily_digest(
            episodes, day, cfg.tz,
            today=today, links=cfg.show_links, max_items=cfg.max_items,
        )

    # --- команды ---

    def answer(self, command: str, argument: str) -> str | None:
        cfg = self._cfg
        try:
            if command == "help":
                return render.HELP
            if command == "today":
                return self.digest()
            if command == "tomorrow":
                return self.digest(self.now().date() + timedelta(days=1))
            if command == "week":
                return render.week_digest(
                    [ep for ep in self._calendar() if ep.score >= cfg.min_score],
                    self.now().date(), cfg.tz, links=False, max_items=cfg.max_items,
                )
            if command == "search":
                if not argument:
                    return "Что искать? Например: аниме врата стейнса"
                return render.search_results(argument, search(argument, client=self._web))
        except ShikimoriError as exc:
            log.warning("Shikimori не ответил: %s", exc)
            return BROKEN
        return None

    def _handle(self, update: dict) -> None:
        if update.get("type") != "message_new":
            return

        obj = update.get("object") or {}
        message = obj.get("message") or obj
        # Свои же сообщения приходят обратно: у сообщества from_id отрицательный.
        if int(message.get("from_id", 0)) <= 0:
            return

        parsed = parse(message.get("text", ""))
        if not parsed:
            return

        command, argument = parsed
        log.info("Команда %s от %s", command, message.get("from_id"))
        reply = self.answer(command, argument)
        if reply:
            self._vk.send_message(int(message["peer_id"]), reply)

    # --- ежедневная рассылка ---

    def due(self, moment: datetime) -> bool:
        """Пора ли слать дайджест за сегодня."""
        hour, minute = self._cfg.daily
        if (moment.hour, moment.minute) < (hour, minute):
            return False
        return self._state.last_digest != moment.date()

    def send_digest(self, day: date | None = None, *, remember: bool = True) -> None:
        day = day or self.now().date()
        text = self.digest(day)
        self._vk.send_message(self._cfg.peer_id, text)
        if remember:
            self._state.mark_digest(day)
        log.info("Дайджест за %s отправлен", day)

    # --- главный цикл ---

    def run(self) -> None:
        poll = LongPoll(self._vk, self._cfg.group_id)
        log.info("Бот слушает беседу %s", self._cfg.peer_id)

        while True:
            try:
                if self.due(self.now()):
                    self.send_digest()
                for update in poll.check():
                    self._handle(update)
            except KeyboardInterrupt:
                log.info("Останавливаюсь")
                return
            except ShikimoriError as exc:
                log.warning("Расписание недоступно: %s", exc)
                time.sleep(60)
            except Exception:
                # Бот обязан пережить любую единичную ошибку: упавший процесс
                # это молчание в беседе до тех пор, пока кто-то не заметит.
                log.exception("Непредвиденная ошибка, продолжаем через минуту")
                time.sleep(60)
