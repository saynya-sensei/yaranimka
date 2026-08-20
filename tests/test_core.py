"""Тесты разбора команд, фильтров расписания и текстов сообщений."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from yaranimka import render, shikimori
from yaranimka.commands import parse
from yaranimka.config import Config, chat_peer_id, parse_time
from yaranimka.shikimori import Episode, _parse, on_day
from yaranimka.vk import split_message

TZ = timezone(timedelta(hours=4))


def episode(hour: int, *, day: int = 19, title: str = "Тайтл", num: int = 7, score: float = 7.5) -> Episode:
    return Episode(
        anime_id=1,
        title=title,
        episode=num,
        at=datetime(2026, 8, day, hour, 0, tzinfo=TZ),
        score=score,
        url="https://shikimori.one/animes/1",
    )


class TestCommands:
    @pytest.mark.parametrize("text,expected", [
        ("!Сегодня", ("today", "")),
        ("/завтра", ("tomorrow", "")),
        ("!неделя", ("week", "")),
        ("/помощь!", ("help", "")),
        ("! сегодня", ("today", "")),
        ("/аниме врата стейнса", ("search", "врата стейнса")),
        ("[club123|Яранимка], сегодня", ("today", "")),
        ("[club123|@yaranimka] аниме наруто", ("search", "наруто")),
        ("[club123|Яранимка] /завтра", ("tomorrow", "")),
    ])
    def test_recognises(self, text, expected):
        assert parse(text) == expected

    @pytest.mark.parametrize("text", [
        "сегодня",
        "завтра приходи пораньше",
        "аниме какое посоветуете?",
        "на неделя не приду",
        "помощь нужна?",
    ])
    def test_bare_words_are_just_conversation(self, text):
        # Бот админ беседы и видит все сообщения: без явного обращения он
        # отвечал бы на обычный разговор.
        assert parse(text) is None

    @pytest.mark.parametrize("text", ["", "   ", "привет", "[club123|Яранимка]",
                                      "/сегодняшний день", "!привет"])
    def test_ignores_everything_else(self, text):
        assert parse(text) is None


class TestConfig:
    def test_chat_number_becomes_peer_id(self):
        assert chat_peer_id(5) == 2_000_000_005

    def test_ready_peer_id_stays(self):
        assert chat_peer_id("2000000005") == 2_000_000_005

    def test_parse_time(self):
        assert parse_time("09:37") == (9, 37)
        assert parse_time("10") == (10, 0)

    @pytest.mark.parametrize("raw", ["25:00", "10:61", "утром"])
    def test_bad_time_raises(self, raw):
        with pytest.raises(ValueError):
            parse_time(raw)


class TestSchedule:
    def test_on_day_uses_local_date(self):
        # 01:00 по UTC+4 это ещё 21:00 предыдущего дня по UTC:
        # день определяем по местному времени, иначе серия уедет во вчера.
        late = Episode(1, "Ночной", 3, datetime(2026, 8, 19, 1, 0, tzinfo=TZ), 7.0, "u")
        assert on_day([late], date(2026, 8, 19), TZ) == [late]
        assert on_day([late], date(2026, 8, 18), TZ) == []

    def test_min_score_filters(self):
        weak, strong = episode(12, score=5.0), episode(13, score=8.0)
        assert on_day([weak, strong], date(2026, 8, 19), TZ, min_score=6.5) == [strong]

    def test_parse_entry(self):
        ep = _parse({
            "next_episode": 7,
            "next_episode_at": "2026-08-19T15:00:00.000+03:00",
            "anime": {"id": 62513, "name": "Clevatess II", "russian": "Клеватесс 2",
                      "url": "/animes/62513-clevatess", "score": "7.71"},
        })
        assert ep is not None
        assert (ep.title, ep.episode, ep.score) == ("Клеватесс 2", 7, 7.71)
        assert ep.url == f"{shikimori.API}/animes/62513-clevatess"
        assert ep.local_time(TZ) == "16:00"

    @pytest.mark.parametrize("entry", [
        {"anime": {"id": 1}},
        {"next_episode_at": "2026-08-19T15:00:00+03:00"},
        {"next_episode_at": "завтра", "anime": {"id": 1}},
    ])
    def test_broken_entries_dropped(self, entry):
        assert _parse(entry) is None

    def test_falls_back_to_romaji(self):
        ep = _parse({"next_episode_at": "2026-08-19T15:00:00+03:00",
                     "anime": {"id": 1, "name": "Clevatess", "russian": ""}})
        assert ep is not None and ep.title == "Clevatess"


class TestRender:
    def test_plural(self):
        assert [render.plural(n, "серия", "серии", "серий") for n in (1, 2, 5, 11, 21, 114)] == \
               ["серия", "серии", "серий", "серий", "серия", "серий"]

    def test_daily_says_today(self):
        text = render.daily_digest([episode(15)], date(2026, 8, 19), TZ,
                                   today=date(2026, 8, 19), hour=10)
        assert text.startswith("Доброе утро, любимые накамычи! 🌸")
        assert "Среда, 19 августа — сегодня выходит 1 серия онгоингов" in text
        assert "• Тайтл — 7 серия, 15:00" in text

    def test_daily_names_other_days(self):
        text = render.daily_digest([episode(15, day=20)], date(2026, 8, 20), TZ, today=date(2026, 8, 19))
        # Не сегодня — значит и слова «сегодня» в заголовке быть не должно.
        assert "Четверг, 20 августа — выходит 1 серия онгоингов" in text
        assert text.count("20 августа") == 1

    def test_empty_day(self):
        text = render.daily_digest([], date(2026, 8, 19), TZ, today=date(2026, 8, 19))
        assert "ни одной серии" in text
        assert "накамычи" in text

    def test_no_link_without_a_release(self):
        # Ссылка в строке ровно одна и только на раздачу: страница тайтла
        # на Shikimori в беседе никому не нужна.
        text = render.daily_digest([episode(15)], date(2026, 8, 19), TZ)
        assert "http" not in text

    def test_max_items_reports_remainder(self):
        text = render.daily_digest([episode(10 + i) for i in range(5)], date(2026, 8, 19), TZ, max_items=2)
        assert "…и ещё 3 серии" in text

    def test_week_groups_by_day(self):
        text = render.week_digest([episode(12, day=19), episode(12, day=21)], date(2026, 8, 19), TZ)
        assert "Среда, 19 августа" in text and "Пятница, 21 августа" in text

    def test_week_ignores_far_future(self):
        text = render.week_digest([episode(12, day=30)], date(2026, 8, 19), TZ)
        assert "расписание пустое" in text

    def test_search_empty(self):
        assert "Ничего не нашла" in render.search_results("нету", [])

    def test_search_lists(self):
        text = render.search_results("клев", [{"russian": "Клеватесс", "aired_on": "2026-07-08",
                                               "score": "7.71", "url": "/animes/1"}])
        assert "• Клеватесс (2026 · оценка 7.71)" in text


class TestSplit:
    def test_short_stays_whole(self):
        assert split_message("привет") == ["привет"]

    def test_splits_on_line_breaks(self):
        parts = split_message("\n".join("строка" * 10 for _ in range(40)), limit=200)
        assert len(parts) > 1
        assert all(len(part) <= 200 for part in parts)

    def test_splits_unbreakable_line(self):
        parts = split_message("я" * 500, limit=100)
        assert len(parts) == 5


class FakeResponse:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """Клиент по сценарию: исключение — обрыв, ответ — ответ."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0
        self.hosts: list[str] = []

    def get(self, url, params=None, headers=None):
        self.calls += 1
        self.hosts.append(url.split("/api/")[0])
        self.params = dict(params or {})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class TestRetries:
    @pytest.fixture(autouse=True)
    def no_waiting(self, monkeypatch):
        monkeypatch.setattr(shikimori.time, "sleep", lambda _: None)

    def test_survives_a_single_timeout(self):
        # Ровно этот сбой и уронил запуск: WinError 10060 на пути к Shikimori.
        client = FakeClient(httpx.ConnectTimeout("таймаут"), FakeResponse(200, []))
        assert shikimori.fetch_calendar(client) == []
        assert client.calls == 2

    def test_gives_up_after_three(self):
        client = FakeClient(*[httpx.ConnectTimeout("таймаут")] * 3)
        with pytest.raises(shikimori.ShikimoriError, match="недоступен"):
            shikimori.fetch_calendar(client)
        assert client.calls == 3

    def test_server_error_is_retried(self):
        client = FakeClient(FakeResponse(502), FakeResponse(200, []))
        assert shikimori.fetch_calendar(client) == []
        assert client.calls == 2

    def test_client_error_is_not_retried(self):
        # 404 от повторов не поправится, ждать бессмысленно.
        client = FakeClient(FakeResponse(404))
        with pytest.raises(shikimori.ShikimoriError, match="404"):
            shikimori.fetch_calendar(client)
        assert client.calls == 1

    def test_unexpected_shape_is_an_error(self):
        with pytest.raises(shikimori.ShikimoriError, match="формате"):
            shikimori.fetch_calendar(FakeClient(FakeResponse(200, {"oops": 1})))

    def test_search_retries_too(self):
        client = FakeClient(httpx.ReadTimeout("таймаут"), FakeResponse(200, [{"id": 1}]))
        assert shikimori.search("клев", client=client) == [{"id": 1}]

    def test_403_moves_to_another_mirror(self):
        # 403 у Shikimori — защита от ботов, а не отказ: другое зеркало
        # на тот же запрос отвечает нормально.
        client = FakeClient(FakeResponse(403), FakeResponse(200, []))
        assert shikimori.fetch_calendar(client) == []
        assert client.calls == 2

    def test_mirrors_rotate_between_attempts(self):
        client = FakeClient(FakeResponse(403), FakeResponse(403), FakeResponse(200, []))
        assert shikimori.fetch_calendar(client) == []
        assert client.hosts == [shikimori.MIRRORS[0], shikimori.MIRRORS[1], shikimori.MIRRORS[0]]

    def test_working_mirror_goes_first(self):
        # В ссылки идёт то же зеркало, на которое ходит бот.
        assert shikimori.API == shikimori.MIRRORS[0] == "https://shikimori.io"


class TestGreeting:
    @pytest.mark.parametrize("hour,expected", [
        (7, "Доброе утро"), (11, "Доброе утро"),
        (12, "Добрый день"), (17, "Добрый день"),
        (18, "Добрый вечер"), (22, "Добрый вечер"),
        (23, "Доброй ночи"), (3, "Доброй ночи"),
    ])
    def test_by_hour(self, hour, expected):
        assert render.greeting(hour) == expected

    def test_digest_greets_by_hour(self):
        text = render.daily_digest([episode(15)], date(2026, 8, 19), TZ, hour=20)
        assert text.startswith("Добрый вечер, любимые накамычи! 🌸")


class TestAdultFilter:
    """Параметр censored у Shikimori: он про «прятать», а не «показывать»."""

    def test_flag_is_inverted(self):
        assert shikimori.flag(adult=True) == "false"
        assert shikimori.flag(adult=False) == "true"

    def test_search_shows_adult_by_default(self):
        # Фильтр урезает выдачу молча — поиск «кайт» терял «Кайт — девочку-
        # убийцу», ничем не показывая, что список неполон.
        client = FakeClient(FakeResponse(200, []))
        shikimori.search("кайт", client=client)
        assert client.params["censored"] == "false"

    @pytest.mark.parametrize("call", [
        lambda c: shikimori.fetch_calendar(c),
        lambda c: shikimori.fetch_season("fall_2026", client=c),
    ])
    def test_digest_hides_adult_by_default(self, call):
        # Утренний пост приходит всей беседе без спроса.
        client = FakeClient(FakeResponse(200, []))
        call(client)
        assert client.params["censored"] == "true"

    @pytest.mark.parametrize("call,expected", [
        (lambda c: shikimori.search("кайт", client=c, adult=False), "true"),
        (lambda c: shikimori.fetch_calendar(c, adult=True), "false"),
        (lambda c: shikimori.fetch_season("fall_2026", client=c, adult=True), "false"),
    ])
    def test_both_sides_can_be_flipped(self, call, expected):
        client = FakeClient(FakeResponse(200, []))
        call(client)
        assert client.params["censored"] == expected


class TestTimezone:
    def test_default_is_the_chat_timezone(self, monkeypatch):
        # Беседа живёт по Ярославлю, то есть по Москве: UTC+3. При UTC+4
        # пост в 10:00 выходил в чате в 09:00.
        monkeypatch.setenv("YARANIMKA_TZ_OFFSET", "")
        assert Config().tz_offset == 3

    def test_offset_is_taken_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("YARANIMKA_TZ_OFFSET", "4")
        assert Config().tz_offset == 4
