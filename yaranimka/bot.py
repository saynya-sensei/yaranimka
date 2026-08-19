"""Бот: дайджест онгоингов, слежение за русскими раздачами и команды беседы."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import httpx

from . import render, torrents
from .commands import parse
from .config import Config
from .shikimori import (Episode, HEADERS, News, ShikimoriError,
                        fetch_calendar, fetch_news, on_day, search)
from .state import State
from .vk import LongPoll, VKClient

log = logging.getLogger(__name__)

BROKEN = "Расписание сейчас не отвечает, попробуйте позже."

# Нашлись и озвучка, и субтитры — ждать больше нечего.
COMPLETE = {torrents.DUB, torrents.SUB}


class Bot:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._vk = VKClient(cfg.vk_token, dry_run=cfg.dry_run)
        self._web = httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True)
        self._state = State(cfg.state_path)
        self._checked_at: datetime | None = None

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

    def episodes_on(self, day: date) -> list[Episode]:
        return on_day(self._calendar(), day, self._cfg.tz, min_score=self._cfg.min_score)

    def digest(self, day: date | None = None, *, updated: str | None = None) -> str:
        """Дневной список — из сохранённого снимка, если он есть.

        Календарь в течение дня тает: вышедшую серию Shikimori переводит на
        следующую неделю. Пока снимок дня сохранён, отвечаем по нему — тогда
        и команда «сегодня», и закреплённое сообщение показывают одно и то же,
        вместе с уже найденными раздачами.
        """
        cfg = self._cfg
        today = self.now().date()
        day = day or today

        rows = self._state.watching(day)
        if rows:
            episodes = [row.to_episode() for row in rows]
            releases = {row.key: row.releases for row in rows}
        else:
            episodes, releases = self.episodes_on(day), {}

        # Новости берём из снимка: при правке сообщения они меняться не должны.
        # Снимка нет — значит это предпросмотр, и их можно запросить свежими.
        stored = self._state.digest_news if day == self._state.last_digest else []
        news = stored or (self.news() if day == today and not rows else [])

        return render.daily_digest(
            episodes, day, cfg.tz,
            today=today, max_items=cfg.max_items,
            releases=releases, news=news,
            hour=self.now().hour, updated=updated,
        )

    def news(self) -> list[News]:
        """Новости — довесок к дайджесту, и падать из-за них нельзя."""
        if self._cfg.news_items <= 0:
            return []
        try:
            return fetch_news(self._cfg.news_items, self._web)
        except ShikimoriError as exc:
            log.warning("Новости не пришли: %s", exc)
            return []

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
                    self.now().date(), cfg.tz, max_items=cfg.max_items,
                )
            if command == "releases":
                return render.watch_status(self._state.watching(), cfg.tz)
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
        cfg = self._cfg
        today = self.now().date()
        day = day or today
        episodes = self.episodes_on(day)
        news = self.news()

        message_id = self._vk.send_message(cfg.peer_id, render.daily_digest(
            episodes, day, cfg.tz,
            today=today, max_items=cfg.max_items,
            news=news, hour=self.now().hour,
        ))

        # Дневной список сохраняем целиком: править сообщение потом будет
        # нечем — как только серия выйдет, Shikimori уберёт её из календаря.
        if cfg.watch:
            self.remember(episodes, day)
        if remember:
            self._state.mark_digest(day, message_id, news)
        self._state.save()
        log.info("Дайджест за %s отправлен (сообщение %s)", day, message_id)

    # --- раздачи ---

    def remember(self, episodes: list[Episode], day: date) -> int:
        added = sum(self._state.watch(ep, day) for ep in episodes)
        if added:
            log.info("В списке дня %s серий", added)
        return added

    def check_releases(self) -> int:
        """Один обход раздач. Возвращает число новых находок."""
        now = self.now()
        deadline = timedelta(days=self._cfg.watch_days)

        pending = []
        for item in self._state.watching():
            if now - item.aired > deadline:
                # За отведённый срок раздача либо появилась, либо её не будет,
                # а сообщение того дня всё равно уже не поправить.
                self._state.unwatch(item.key)
            elif now >= item.aired and not COMPLETE <= item.kinds:
                pending.append(item)

        if not pending:
            self._state.save()
            return 0

        nyaa = torrents.nyaa_feed(self._web)
        anilibria = torrents.anilibria_feed(self._web)
        if not nyaa and not anilibria:
            log.warning("Обе площадки молчат, пропускаю обход")
            return 0

        fresh = 0
        for item in pending:
            found = torrents.find(
                item.titles, item.episode,
                nyaa=nyaa, anilibria=anilibria, client=self._web,
            )
            unseen = [release for release in found if release.kind not in item.kinds]
            for release in unseen:
                self._state.add_release(item.key, release)
                log.info("Нашлась раздача: %s, %s серия — %s",
                         item.title, item.episode, release.label)
            fresh += len(unseen)

        self._state.save()
        if fresh:
            self.refresh_digest()
        return fresh

    def refresh_digest(self) -> bool:
        """Переписывает сегодняшнее сообщение — новыми ссылками на раздачи.

        Второго сообщения бот не отправляет никогда: если правка не прошла,
        ссылки просто дождутся следующего дайджеста.
        """
        day = self._state.last_digest
        message_id = self._state.digest_message
        if not day or not message_id or not self._state.watching(day):
            return False

        text = self.digest(day, updated=self.now().strftime("%H:%M"))
        return self._vk.edit_message(self._cfg.peer_id, message_id, text)

    def check_due(self, moment: datetime) -> bool:
        if not self._cfg.watch:
            return False
        if self._checked_at is None:
            return True
        return moment - self._checked_at >= timedelta(minutes=self._cfg.watch_every)

    # --- главный цикл ---

    def run(self) -> None:
        poll = LongPoll(self._vk, self._cfg.group_id)
        log.info("Бот слушает беседу %s", self._cfg.peer_id)

        while True:
            try:
                now = self.now()
                if self.due(now):
                    self.send_digest()
                if self.check_due(now):
                    self._checked_at = now
                    self.check_releases()
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
