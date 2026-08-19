"""Тесты списка слежения и сообщений о найденных раздачах."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from yaranimka import render
from yaranimka.shikimori import Episode
from yaranimka.state import State
from yaranimka.torrents import DUB, SUB, Release

TZ = timezone(timedelta(hours=4))
AIRED = datetime(2026, 8, 19, 16, 0, tzinfo=TZ)


def watched(state: State, key: str = "1:7") -> None:
    state.watch(key, title="Клеватесс 2", romaji="Clevatess II", episode=7, aired=AIRED)


class TestState:
    def test_watch_survives_restart(self, tmp_path):
        path = tmp_path / "state.json"
        first = State(path)
        watched(first)
        first.mark_found("1:7", DUB)
        first.save()

        again = State(path).watching()
        assert len(again) == 1
        assert again[0].titles == ["Clevatess II", "Клеватесс 2"]
        assert again[0].found == [DUB] and again[0].aired == AIRED

    def test_watch_is_idempotent(self, tmp_path):
        state = State(tmp_path / "state.json")
        assert state.watch("1:7", title="a", romaji="b", episode=7, aired=AIRED) is True
        assert state.watch("1:7", title="a", romaji="b", episode=7, aired=AIRED) is False

    def test_mark_found_does_not_duplicate(self, tmp_path):
        state = State(tmp_path / "state.json")
        watched(state)
        state.mark_found("1:7", DUB)
        state.mark_found("1:7", DUB)
        assert state.watching()[0].found == [DUB]

    def test_unwatch(self, tmp_path):
        state = State(tmp_path / "state.json")
        watched(state)
        state.unwatch("1:7")
        assert state.watching() == []

    def test_broken_entry_is_dropped(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"watch": {"1:7": {"title": "x"}}}', encoding="utf-8")
        assert State(path).watching() == []

    def test_old_state_without_watch_still_loads(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"last_digest": "2026-08-19"}', encoding="utf-8")
        state = State(path)
        assert state.last_digest.isoformat() == "2026-08-19"
        assert state.watching() == []


class TestEpisodeKey:
    def test_key_is_stable_per_episode(self):
        ep = Episode(62513, "Клеватесс 2", 7, AIRED, 7.7, "u", romaji="Clevatess II")
        assert ep.key == "62513:7"
        assert ep.titles == ["Clevatess II", "Клеватесс 2"]


class TestAlerts:
    def test_alert_lists_every_kind(self):
        text = render.release_alert("Клеватесс 2", 7, [
            Release(DUB, "AniLibria", "Clevatess - AniLiberty [1080p][1-7]", "https://a", 27, "1.2 ГБ"),
            Release(SUB, "Nyaa", "Clevatess 2 - 07 [RUS Sub]", "https://n", 5),
        ])
        assert "Клеватесс 2, 7 серия — появилась озвучка и субтитры" in text
        assert "AniLibria · 27 сидов · 1.2 ГБ" in text
        assert "https://a" in text and "https://n" in text

    def test_alert_without_seeders_or_size(self):
        text = render.release_alert("Тайтл", 3, [Release(DUB, "AniLibria", "имя", "https://a")])
        assert "·" not in text.split("\n")[-2]

    def test_status_shows_what_is_found(self, tmp_path):
        state = State(tmp_path / "state.json")
        watched(state)
        state.watch("2:9", title="Тройной шторм", romaji="Thunder 3", episode=9, aired=AIRED)
        state.mark_found("1:7", DUB)

        text = render.watch_status(state.watching(), TZ)
        assert "Клеватесс 2, 7 серия (19.08 16:00) — 🔊 озвучка" in text
        assert "Тройной шторм, 9 серия (19.08 16:00) — пока ничего" in text

    def test_status_when_empty(self):
        assert "ничего не жду" in render.watch_status([], TZ)
