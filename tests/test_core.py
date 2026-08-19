"""Тесты разбора команд, фильтров расписания и текстов сообщений."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from yaranimka import render
from yaranimka.commands import parse
from yaranimka.config import chat_peer_id, parse_time
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
        ("сегодня", ("today", "")),
        ("!Сегодня", ("today", "")),
        ("/завтра", ("tomorrow", "")),
        ("неделя", ("week", "")),
        ("помощь!", ("help", "")),
        ("аниме врата стейнса", ("search", "врата стейнса")),
        ("[club123|Яранимка], сегодня", ("today", "")),
        ("[club123|@yaranimka] аниме наруто", ("search", "наруто")),
    ])
    def test_recognises(self, text, expected):
        assert parse(text) == expected

    @pytest.mark.parametrize("text", ["", "   ", "привет", "[club123|Яранимка]", "сегодняшний день"])
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
        assert ep.url == "https://shikimori.one/animes/62513-clevatess"
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
        text = render.daily_digest([episode(15)], date(2026, 8, 19), TZ, today=date(2026, 8, 19))
        assert text.startswith("🌸 Сегодня, 19 августа — 1 серия")
        assert "• Тайтл — 7 серия, 15:00" in text

    def test_daily_names_other_days(self):
        text = render.daily_digest([episode(15, day=20)], date(2026, 8, 20), TZ, today=date(2026, 8, 19))
        assert text.startswith("🌸 Четверг, 20 августа — 1 серия")
        # Дата в заголовке ровно одна: «Четверг, 20 августа, 20 августа» — это баг.
        assert text.count("20 августа") == 1

    def test_empty_day(self):
        text = render.daily_digest([], date(2026, 8, 19), TZ, today=date(2026, 8, 19))
        assert "Ни одной серии" in text

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
