"""Тесты поиска русских раздач: язык, вид перевода, номер серии, сопоставление."""

from __future__ import annotations

from xml.etree import ElementTree

import pytest

from yaranimka import torrents
from yaranimka.torrents import ANY, DUB, SUB, Release, classify, covers_episode, title_matches, variants

YOUJO = ["Youjo Senki II", "Военная хроника маленькой девочки 2"]
CLEVATESS = ["Clevatess II: Majuu no Ou to Itsuwari no Yuusha Denshou", "Клеватесс 2: Король демонических зверей"]


class TestClassify:
    @pytest.mark.parametrize("name,kind", [
        ("[AniLibria.TV] Youjo Senki 2 - 07 [WEBRip 1080p]", DUB),
        ("[SHIZA Project] Clevatess 2 TV - 07 [1080p]", DUB),
        ("Военная хроника 2 - 07 [Русская озвучка]", DUB),
        ("Youjo Senki 2 - 07 [RUS Sub]", SUB),
        ("Аниме - 07 [русские субтитры]", SUB),
        ("Youjo Senki 2 - 07 [RUS]", ANY),
    ])
    def test_detects_kind(self, name, kind):
        assert classify(name) == kind

    @pytest.mark.parametrize("name", [
        "[SubsPlease] Youjo Senki 2 - 07 (1080p) [ABCD1234].mkv",
        "[Erai-raws] Clevatess - 07 [1080p][Multiple Subtitle]",
        "[Legendas] Anime - 07 [PT-BR]",
    ])
    def test_ignores_foreign(self, name):
        assert classify(name) is None

    def test_rurouni_is_not_russian(self):
        # «\bru» поймал бы «Rurouni» — поэтому ищем именно корень rus.
        assert classify("[Erai-raws] Rurouni Kenshin - 07 [1080p]") is None


class TestEpisodeNumber:
    @pytest.mark.parametrize("name", [
        "[AniLibria] Youjo Senki 2 - 07 [WEBRip 1080p]",
        "[SHIZA] Clevatess [07] [1080p]",
        "Youjo Senki S02E07 [RUS]",
        "Военная хроника, 7 серия",
        "[Group] Anime - 07v2 [1080p]",
    ])
    def test_finds_single(self, name):
        assert covers_episode(name, 7)
        assert not covers_episode(name, 8)

    @pytest.mark.parametrize("name", [
        "Iwamoto-senpai no Suisen - AniLiberty [WEB-DL 1080p][HEVC][1-6]",
        "[SHIZA] Clevatess TV (01-07) [1080p]",
    ])
    def test_finds_range(self, name):
        assert covers_episode(name, 5)

    def test_range_has_edges(self):
        name = "[SHIZA] Clevatess TV [01-07] [1080p]"
        assert covers_episode(name, 1) and covers_episode(name, 7)
        assert not covers_episode(name, 8)

    def test_resolution_is_not_an_episode(self):
        assert not covers_episode("[Group] Anime - 05 [1080p][x264][10bit]", 108)
        assert not covers_episode("[Group] Anime - 05 [720p]", 720)

    def test_zero_never_matches(self):
        assert not covers_episode("[Group] Anime - 07", 0)


class TestTitleMatching:
    def test_roman_and_arabic_seasons_are_equal(self):
        assert title_matches("[AniLibria] Youjo Senki 2 - 07 [1080p]", variants(YOUJO))

    def test_long_shikimori_name_matches_short_release(self):
        # Полное имя с Shikimori длиннее любого имени раздачи — спасает
        # укороченный вариант до двоеточия.
        assert title_matches("[SHIZA Project] Clevatess 2 - 07 [1080p]", variants(CLEVATESS))

    def test_russian_name_matches(self):
        assert title_matches("Военная хроника маленькой девочки 2 - 07 [Озвучка]", variants(YOUJO))

    def test_other_season_does_not_match(self):
        assert not title_matches("[AniLibria] Youjo Senki - 07 [1080p]", variants(YOUJO))

    def test_other_show_does_not_match(self):
        assert not title_matches("[AniLibria] Gachiakuta 2 - 07 [1080p]", variants(YOUJO))

    def test_variants_keep_order_without_duplicates(self):
        assert variants(["Clevatess II: Majuu no Ou", "Clevatess II: Majuu no Ou"]) == \
               ["Clevatess II: Majuu no Ou", "Clevatess II"]

    def test_colon_inside_a_name_is_not_a_subtitle(self):
        # «Re:Zero» резать нельзя: обрезок «Re» совпадал с чем угодно.
        assert variants(["Re:Zero kara Hajimeru Isekai Seikatsu 4th Season"]) == \
               ["Re:Zero kara Hajimeru Isekai Seikatsu 4th Season"]

    def test_rezero_does_not_match_unrelated_show(self):
        rezero = variants(["Re:Zero kara Hajimeru Isekai Seikatsu 4th Season"])
        assert not title_matches("Kabushikigaisha Magi-Lumière 2nd Season", rezero)

    def test_accented_letters_stay_whole(self):
        # «Lumière» распадалось на «lumi» и «re» — отсюда и бралось ложное «re».
        assert torrents.tokens("Magi-Lumière") == {"magi", "lumière"}

    def test_ordinal_and_digit_seasons_are_equal(self):
        assert torrents.tokens("Isekai 4th Season") == torrents.tokens("Isekai 4")

    def test_short_one_word_target_is_refused(self):
        assert not title_matches("Что угодно про re и прочее", ["Re"])


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel>
<item>
  <title>[AniLibria.TV] Youjo Senki 2 - 07 [WEBRip 1080p]</title>
  <guid>https://nyaa.si/view/111</guid>
  <nyaa:seeders>42</nyaa:seeders>
  <nyaa:size>1.4 GiB</nyaa:size>
</item>
<item>
  <title>Youjo Senki 2 - 07 [RUS Sub]</title>
  <guid>https://nyaa.si/view/222</guid>
  <nyaa:seeders>7</nyaa:seeders>
  <nyaa:size>700 MiB</nyaa:size>
</item>
<item>
  <title>[Legendas] Youjo Senki 2 - 07 [PT-BR]</title>
  <guid>https://nyaa.si/view/333</guid>
  <nyaa:seeders>3</nyaa:seeders>
  <nyaa:size>1.0 GiB</nyaa:size>
</item>
</channel></rss>"""


class TestFeedAndSearch:
    def test_parse_keeps_only_russian(self):
        found = torrents.parse_nyaa(ElementTree.fromstring(RSS))
        assert [r.kind for r in found] == [DUB, SUB]
        assert found[0].seeders == 42 and found[0].size == "1.4 GiB"
        assert found[0].url == "https://nyaa.si/view/111"

    def test_find_picks_one_per_kind(self):
        nyaa = torrents.parse_nyaa(ElementTree.fromstring(RSS))
        found = torrents.find(YOUJO, 7, nyaa=nyaa, anilibria=[])
        assert [r.kind for r in found] == [DUB, SUB]

    def test_find_prefers_more_seeders(self):
        weak = Release(DUB, "Nyaa", "[AniLibria] Youjo Senki 2 - 07", "u1", seeders=2)
        strong = Release(DUB, "Nyaa", "[SHIZA] Youjo Senki 2 - 07", "u2", seeders=90)
        found = torrents.find(YOUJO, 7, nyaa=[weak, strong], anilibria=[])
        assert found[0].url == "u2"

    def test_find_ignores_other_episodes(self):
        nyaa = torrents.parse_nyaa(ElementTree.fromstring(RSS))
        assert torrents.find(YOUJO, 8, nyaa=nyaa, anilibria=[]) == []

    def test_anilibria_needs_client(self):
        feed = [{"id": 1, "alias": "youjo-senki-ii", "name": {"english": "Youjo Senki 2"},
                 "latest_episode": {"ordinal": 7}}]
        # Без клиента за торрентами не сходить, поэтому и находки нет.
        assert torrents.find(YOUJO, 7, nyaa=[], anilibria=feed, client=None) == []

    def test_release_labels(self):
        assert Release(DUB, "Nyaa", "n", "u").label == "озвучка"
        assert Release(SUB, "Nyaa", "n", "u").icon == "📝"
