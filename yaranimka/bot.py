"""Бот: дайджест онгоингов, слежение за русскими раздачами и команды беседы."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta

import httpx

from . import config, render, torrents
from .__init__ import __version__
from .commands import parse
from .config import Config
from .shikimori import (Episode, HEADERS, News, ShikimoriError,
                        days_until_next_season, fetch_calendar, fetch_news,
                        fetch_season, on_day, search, season_of, season_start)
from .state import State
from .vk import LongPoll, VKClient

log = logging.getLogger(__name__)

BROKEN = "Расписание сейчас не отвечает, попробуйте позже."

# Нашлись и озвучка, и субтитры — ждать больше нечего.
COMPLETE = {torrents.DUB, torrents.SUB}

# Сколько дней держать поздравление с новым сезоном. Один день был бы честнее,
# но пропущенный запуск стоил бы тогда целого квартала ожидания.
OPENING_DAYS = 3

# За сколько дней до смены сезона начинать напоминать про торренты.
FAREWELL_DAYS = 7


class Bot:
    def __init__(self, cfg: Config, *, vk=None, web=None):
        # Клиенты можно передать снаружи: создание SSL-контекста стоит заметных
        # долей секунды, и тестам ни к чему платить её на каждый случай.
        self._cfg = cfg
        self._vk = vk or VKClient(cfg.vk_token, dry_run=cfg.dry_run)
        self._web = web or httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True)
        self._state = State(cfg.state_path)
        self._checked_at: datetime | None = None
        self._names: dict[int, str] = {}

    def close(self) -> None:
        self._vk.close()
        self._web.close()

    def __enter__(self) -> "Bot":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- расписание ---

    def _calendar(self) -> list[Episode]:
        return fetch_calendar(self._web, adult=self._cfg.adult)

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

        # Новости и сезонный блок берём из снимка: при правке сообщения они
        # меняться не должны. Снимка нет — значит это предпросмотр, и всё
        # можно запросить свежим.
        mine = day == self._state.last_digest
        preview = day == today and not rows
        news = (self._state.digest_news if mine else []) or (self.news() if preview else [])
        season = (self._state.digest_season if mine else "") or (self.season(day) if preview else "")

        return render.daily_digest(
            episodes, day, cfg.tz,
            today=today, max_items=cfg.max_items,
            releases=releases, news=news, season=season,
            hour=self.now().hour, updated=updated,
        )

    def season(self, day: date) -> str:
        """Сезонный блок: проводы уходящего сезона или встреча нового.

        Оба события редкие — четыре раза в год, — поэтому запрос к Shikimori
        уходит только в эти дни, а не при каждом дайджесте.
        """
        cfg = self._cfg
        if not cfg.season:
            return ""

        left = days_until_next_season(day)
        if 1 <= left <= FAREWELL_DAYS:
            return render.season_farewell(left)

        if (day - season_start(day)).days < OPENING_DAYS:
            code, name, year = season_of(day)
            try:
                titles = fetch_season(code, cfg.season_top, self._web, adult=cfg.adult)
                return render.season_opening(titles, name, year)
            except ShikimoriError as exc:
                log.warning("Сезонная подборка не пришла: %s", exc)
        return ""

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
                return render.search_results(
                    argument, search(argument, client=self._web, adult=cfg.adult))
        except ShikimoriError as exc:
            log.warning("Shikimori не ответил: %s", exc)
            return BROKEN
        return None

    def _handle(self, update: dict) -> None:
        if self._cfg.debug:
            log.info("СОБЫТИЕ: %s", json.dumps(update, ensure_ascii=False))

        if update.get("type") != "message_new":
            # Молчать тут нельзя: если ВКонтакте пришлёт выход из беседы
            # отдельным типом события, без этой строки мы никогда не узнаем.
            log.info("Событие %s пропущено", update.get("type"))
            return

        obj = update.get("object") or {}
        message = obj.get("message") or obj

        # Служебные события беседы приходят теми же message_new, но с action.
        if message.get("action"):
            self.guard(message)
            return

        if not self._cfg.commands:
            return

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

    # --- охрана беседы ---

    def guard(self, message: dict) -> None:
        """Служебные события беседы: кто вышел и кто вернулся.

        ВКонтакте перестал показывать сообщения о выходе, но событие боту
        по-прежнему приходит. Отличаем добровольный уход от исключения по
        совпадению member_id и from_id: человек сам себя не выгоняет.
        """
        cfg = self._cfg
        action = message.get("action") or {}
        kind = action.get("type")
        peer_id = int(message.get("peer_id") or cfg.peer_id)
        log.info("Событие беседы %s: %s", kind, json.dumps(action, ensure_ascii=False))

        try:
            member = int(action.get("member_id") or 0)
            author = int(message.get("from_id") or 0)
        except (TypeError, ValueError):
            return

        # Сообщества в беседе приходят отрицательным id — их не трогаем.
        if member <= 0:
            return

        if kind == "chat_kick_user":
            if member != author:
                # Исключил админ: возвращаться ему и так нельзя.
                self._state.forget_left(member)
                self._state.save()
                return
            self.on_left(peer_id, member)

        elif kind in ("chat_invite_user", "chat_invite_user_by_link"):
            if member != author:
                # Пригласили намеренно — снимаем метку и пускаем.
                self._state.forget_left(member)
                self._state.save()
            elif self._state.has_left(member):
                self.on_returned(peer_id, member)

    def on_left(self, peer_id: int, member: int) -> None:
        cfg = self._cfg
        log.info("Из беседы вышел %s", member)
        self._state.mark_left(member, self.now())
        self._state.save()

        # Кик уже вышедшего — попытка запретить ему тихо вернуться. Сработает
        # он или нет, зависит от ВКонтакте, поэтому вторая линия обороны —
        # перехват самого возвращения.
        kicked = cfg.leave_kick and self._vk.remove_chat_user(config.chat_number(peer_id), member)
        if cfg.leave_notify:
            self._vk.send_message(peer_id, render.left_notice(member, self.name_of(member), kicked=kicked))

    def on_returned(self, peer_id: int, member: int) -> None:
        cfg = self._cfg
        log.info("Вернулся сам %s — исключаю", member)
        if cfg.leave_kick:
            self._vk.remove_chat_user(config.chat_number(peer_id), member)
        if cfg.leave_notify:
            self._vk.send_message(peer_id, render.returned_notice(member, self.name_of(member)))

    def name_of(self, user_id: int) -> str:
        """Имя участника с запоминанием: одного и того же спрашиваем однажды."""
        if user_id not in self._names:
            self._names[user_id] = self._vk.user_name(user_id)
        return self._names[user_id]

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
        news, season = self.news(), self.season(day)

        message_id = self._vk.send_message(cfg.peer_id, render.daily_digest(
            episodes, day, cfg.tz,
            today=today, max_items=cfg.max_items,
            news=news, season=season, hour=self.now().hour,
        ))

        # Дневной список сохраняем целиком: править сообщение потом будет
        # нечем — как только серия выйдет, Shikimori уберёт её из календаря.
        if cfg.watch:
            self.remember(episodes, day)
        if remember:
            self._state.mark_digest(day, message_id, news, season)
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
        cfg = self._cfg
        poll = LongPoll(self._vk, cfg.group_id)

        # По этой строке видно, что именно крутится на сервере: без неё
        # «бот не реагирует» и «на сервере старая сборка» неразличимы.
        def onoff(flag: bool) -> str:
            return "вкл" if flag else "выкл"

        log.info(
            "yaranimka %s слушает беседу %s — команды %s, охрана %s, раздачи %s, дайджест в %s",
            __version__, cfg.peer_id, onoff(cfg.commands),
            onoff(cfg.leave_notify or cfg.leave_kick), onoff(cfg.watch), cfg.daily_at,
        )

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
