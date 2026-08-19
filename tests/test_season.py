"""Тесты сезонных блоков: проводы уходящего сезона и встреча нового."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from yaranimka import render
from yaranimka.bot import FAREWELL_DAYS, OPENING_DAYS
from yaranimka.shikimori import (Episode, SeasonTitle, days_until_next_season,
                                 next_season_start, season_of, season_start)

TZ = timezone(timedelta(hours=4))
DAY = date(2026, 8, 19)


def episode() -> Episode:
    return Episode(1, "Тайтл", 7, datetime(2026, 8, 19, 16, 0, tzinfo=TZ), 7.7, "u")


def title(name: str = "Монолог фармацевта 3", starts: date | None = date(2026, 10, 2)) -> SeasonTitle:
    return SeasonTitle(name, "https://shikimori.io/animes/1", "tv", starts)


class TestSeasonMath:
    @pytest.mark.parametrize("day,code,russian", [
        (date(2026, 1, 1), "winter_2026", "зима"),
        (date(2026, 3, 31), "winter_2026", "зима"),
        (date(2026, 4, 1), "spring_2026", "весна"),
        (date(2026, 8, 19), "summer_2026", "лето"),
        (date(2026, 10, 1), "fall_2026", "осень"),
        (date(2026, 12, 31), "fall_2026", "осень"),
    ])
    def test_season_of(self, day, code, russian):
        got_code, got_russian, year = season_of(day)
        assert (got_code, got_russian, year) == (code, russian, 2026)

    @pytest.mark.parametrize("day,start", [
        (date(2026, 8, 19), date(2026, 7, 1)),
        (date(2026, 10, 1), date(2026, 10, 1)),
        (date(2026, 2, 14), date(2026, 1, 1)),
    ])
    def test_season_start(self, day, start):
        assert season_start(day) == start

    def test_next_season_rolls_over_the_year(self):
        # Декабрь смотрит уже в следующий год, иначе «следующий сезон»
        # оказался бы в прошлом.
        assert next_season_start(date(2026, 12, 15)) == date(2027, 1, 1)

    @pytest.mark.parametrize("day,left", [
        (date(2026, 9, 24), 7),
        (date(2026, 9, 30), 1),
        (date(2026, 10, 1), 92),
        (date(2026, 8, 19), 43),
    ])
    def test_days_until(self, day, left):
        assert days_until_next_season(day) == left


class TestFarewell:
    def test_exactly_a_week_says_a_week(self):
        text = render.season_farewell(7)
        assert text.startswith("⏳ У этого аниме-сезона осталась неделя —")
        assert "проверьте торренты на наличие завершившихся тайтлов!" in text

    @pytest.mark.parametrize("days,expected", [
        (1, "остался 1 день"),
        (2, "осталось 2 дня"),
        (5, "осталось 5 дней"),
    ])
    def test_countdown_agrees(self, days, expected):
        # «Осталась неделя» на пятый день была бы неправдой, поэтому счёт.
        assert expected in render.season_farewell(days)


class TestOpening:
    def test_lists_the_season(self):
        text = render.season_opening([title(), title("Чёрный клевер 2", date(2026, 10, 1))],
                                     "осень", 2026)
        assert text.startswith("🎬 Стартовал новый аниме-сезон — осень 2026!")
        assert "• Монолог фармацевта 3, с 2 октября" in text
        assert "• Чёрный клевер 2, с 1 октября" in text

    def test_title_without_a_date(self):
        assert "• Тайтл\n" in render.season_opening([title("Тайтл", None)], "осень", 2026)

    def test_head_alone_when_nothing_found(self):
        assert render.season_opening([], "осень", 2026) == "🎬 Стартовал новый аниме-сезон — осень 2026!"


class TestPlacement:
    def test_block_sits_between_schedule_and_news(self):
        from yaranimka.shikimori import News
        note = News("Трейлер", "https://shikimori.io/forum/news/1",
                    datetime(2026, 8, 19, 15, 0, tzinfo=TZ))
        text = render.daily_digest([episode()], DAY, TZ, today=DAY,
                                   season=render.season_farewell(3), news=[note])
        assert text.index("• Тайтл") < text.index("⏳") < text.index("📰")

    def test_no_block_when_empty(self):
        assert "⏳" not in render.daily_digest([episode()], DAY, TZ, today=DAY)

    def test_season_outlives_news_when_tight(self):
        from yaranimka.shikimori import News
        notes = [News(f"Новость {i}", f"https://shikimori.io/forum/news/{i}",
                      datetime(2026, 8, 19, 15, 0, tzinfo=TZ)) for i in range(5)]
        block = render.season_farewell(3)

        bare = render.daily_digest([episode()], DAY, TZ, today=DAY, season=block)
        text = render.daily_digest([episode()], DAY, TZ, today=DAY,
                                   season=block, news=notes, limit=len(bare) + 20)
        # Новости — довесок к довеску: сезонное напоминание переживает их.
        assert "⏳" in text and "📰" not in text


class TestWindows:
    """Границы окон, в которые блоки вообще появляются."""

    def farewell_days(self, day: date) -> bool:
        return 1 <= days_until_next_season(day) <= FAREWELL_DAYS

    def opening_days(self, day: date) -> bool:
        return (day - season_start(day)).days < OPENING_DAYS

    def test_farewell_covers_the_last_week_only(self):
        assert not self.farewell_days(date(2026, 9, 23))
        assert self.farewell_days(date(2026, 9, 24))
        assert self.farewell_days(date(2026, 9, 30))
        # 1 октября сезон уже новый — прощаться не с чем.
        assert not self.farewell_days(date(2026, 10, 1))

    def test_opening_covers_the_first_days(self):
        assert self.opening_days(date(2026, 10, 1))
        assert self.opening_days(date(2026, 10, 3))
        assert not self.opening_days(date(2026, 10, 4))

    def test_windows_never_overlap(self):
        # Иначе в один день пришли бы оба блока сразу.
        day = date(2026, 1, 1)
        while day < date(2027, 1, 1):
            assert not (self.farewell_days(day) and self.opening_days(day)), day
            day += timedelta(days=1)
