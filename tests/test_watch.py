"""Тесты дневного списка: хранение, ссылки на раздачи, одно сообщение."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from yaranimka import render
from yaranimka.shikimori import Episode
from yaranimka.state import State
from yaranimka.torrents import DUB, SUB, Release

TZ = timezone(timedelta(hours=4))
DAY = date(2026, 8, 19)
AIRED = datetime(2026, 8, 19, 16, 0, tzinfo=TZ)

DUB_RELEASE = Release(DUB, "AniLibria", "Youjo Senki II - AniLiberty [1-7]",
                      "https://anilibria.top/anime/releases/release/youjo-senki-ii", 39, "1.3 ГБ")
SUB_RELEASE = Release(SUB, "Nyaa", "Youjo Senki II - 07 [RUS Sub]", "https://nyaa.si/view/222", 5)


def episode(anime_id: int = 62513, num: int = 7, title: str = "Клеватесс 2", hour: int = 16) -> Episode:
    return Episode(anime_id, title, num, AIRED.replace(hour=hour), 7.7,
                   f"https://shikimori.one/animes/{anime_id}", romaji="Clevatess II")


class TestState:
    def test_row_survives_restart(self, tmp_path):
        path = tmp_path / "state.json"
        first = State(path)
        first.watch(episode(), DAY)
        first.add_release("62513:7", DUB_RELEASE)
        first.mark_digest(DAY, 4242)
        first.save()

        again = State(path)
        assert again.digest_message == 4242
        rows = again.watching(DAY)
        assert len(rows) == 1
        assert rows[0].titles == ["Clevatess II", "Клеватесс 2"]
        assert rows[0].releases[0].url == DUB_RELEASE.url
        assert rows[0].releases[0].seeders == 39

    def test_watch_is_idempotent(self, tmp_path):
        state = State(tmp_path / "state.json")
        assert state.watch(episode(), DAY) is True
        assert state.watch(episode(), DAY) is False

    def test_one_release_per_kind(self, tmp_path):
        state = State(tmp_path / "state.json")
        state.watch(episode(), DAY)
        state.add_release("62513:7", DUB_RELEASE)
        state.add_release("62513:7", DUB_RELEASE)
        state.add_release("62513:7", SUB_RELEASE)
        assert state.watching(DAY)[0].kinds == {DUB, SUB}
        assert len(state.watching(DAY)[0].releases) == 2

    def test_rows_filtered_by_day(self, tmp_path):
        state = State(tmp_path / "state.json")
        state.watch(episode(), DAY)
        state.watch(episode(anime_id=1, num=3), DAY - timedelta(days=1))
        assert len(state.watching(DAY)) == 1
        assert len(state.watching()) == 2

    def test_message_id_belongs_to_its_day(self, tmp_path):
        state = State(tmp_path / "state.json")
        state.mark_digest(DAY, 4242)
        state.mark_digest(DAY + timedelta(days=1), None)
        # Вчерашнее сообщение больше не наше: править его нельзя.
        assert state.digest_message is None

    def test_broken_row_is_dropped(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"watch": {"1:7": {"title": "x"}}}', encoding="utf-8")
        assert State(path).watching() == []

    def test_row_becomes_episode_again(self, tmp_path):
        state = State(tmp_path / "state.json")
        state.watch(episode(), DAY)
        back = state.watching(DAY)[0].to_episode()
        assert (back.key, back.title, back.romaji) == ("62513:7", "Клеватесс 2", "Clevatess II")


class TestDigestWithReleases:
    def test_links_appear_in_the_list(self):
        ep = episode()
        text = render.daily_digest([ep], DAY, TZ, today=DAY, releases={ep.key: [DUB_RELEASE, SUB_RELEASE]})
        assert "  🔊 озвучка: https://anilibria.top/anime/releases/release/youjo-senki-ii" in text
        assert "  📝 субтитры: https://nyaa.si/view/222" in text

    def test_line_without_releases_is_unchanged(self):
        ep = episode()
        text = render.daily_digest([ep], DAY, TZ, today=DAY)
        assert "озвучка" not in text and "http" not in text

    def test_update_time_is_shown(self):
        text = render.daily_digest([episode()], DAY, TZ, today=DAY, updated="19:45")
        assert text.rstrip().endswith("Обновлено в 19:45")


class TestSingleMessage:
    def many(self, count: int) -> list[Episode]:
        return [episode(anime_id=i, num=7, title=f"Тайтл номер {i} " + "длинное название " * 4)
                for i in range(count)]

    def test_always_fits_one_message(self):
        episodes = self.many(40)
        found = {ep.key: [DUB_RELEASE, SUB_RELEASE] for ep in episodes}
        text = render.daily_digest(episodes, DAY, TZ, today=DAY, max_items=40, releases=found, limit=4000)
        assert len(text) <= 4000

    def test_list_is_trimmed_only_as_a_last_resort(self):
        episodes = self.many(40)
        found = {ep.key: [DUB_RELEASE, SUB_RELEASE] for ep in episodes}
        text = render.daily_digest(episodes, DAY, TZ, today=DAY, max_items=40, releases=found, limit=1500)
        assert len(text) <= 1500
        assert "…и ещё" in text

    def test_short_day_keeps_everything(self):
        ep = episode()
        text = render.daily_digest([ep], DAY, TZ, today=DAY, releases={ep.key: [DUB_RELEASE]})
        assert "…и ещё" not in text and "anilibria.top" in text


class TestStatusCommand:
    def test_status_has_details(self, tmp_path):
        state = State(tmp_path / "state.json")
        state.watch(episode(), DAY)
        state.watch(episode(anime_id=1, num=9, title="Тройной шторм", hour=19), DAY)
        state.add_release("62513:7", DUB_RELEASE)

        text = render.watch_status(state.watching(DAY), TZ)
        assert "• Клеватесс 2, 7 серия (19.08 16:00)" in text
        assert "🔊 озвучка · AniLibria · 39 сидов · 1.3 ГБ" in text
        assert "• Тройной шторм, 9 серия (19.08 19:00)\n  пока ничего" in text

    def test_status_when_empty(self):
        assert "ничего не отслеживаю" in render.watch_status([], TZ)
