"""Тесты охраны беседы: кто вышел, кого исключать, кого пропускать."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from yaranimka import render
from yaranimka.bot import Bot
from yaranimka.config import Config, chat_number
from yaranimka.state import State

TZ = timezone(timedelta(hours=4))
PEER = 2_000_000_002
CHAT = 2


class FakeVK:
    """Подменяет клиент VK: записывает, что бот попытался сделать."""

    def __init__(self, kick_ok: bool = True):
        self.kicked: list[tuple[int, int]] = []
        self.sent: list[tuple[int, str]] = []
        self.kick_ok = kick_ok

    def remove_chat_user(self, chat_id: int, member_id: int) -> bool:
        self.kicked.append((chat_id, member_id))
        return self.kick_ok

    def send_message(self, peer_id: int, text: str):
        self.sent.append((peer_id, text))
        return 1

    def user_name(self, user_id: int) -> str:
        return "Иван Иванов"

    def close(self) -> None:
        pass


@pytest.fixture
def bot(tmp_path):
    cfg = Config()
    cfg.vk_token, cfg.group_id, cfg.chat_id = "dummy", 1, str(PEER)
    cfg.state_path = str(tmp_path / "state.json")
    cfg.leave_notify = cfg.leave_kick = True

    # Настоящие HTTP-клиенты тут не нужны, а стоят секунд.
    return Bot(cfg, vk=FakeVK(), web=FakeVK())


def event(kind: str, member: int, author: int | None = None) -> dict:
    """Служебное сообщение беседы в том виде, в каком его шлёт Long Poll."""
    return {
        "type": "message_new",
        "object": {"message": {
            "peer_id": PEER,
            "from_id": member if author is None else author,
            "text": "",
            "action": {"type": kind, "member_id": member},
        }},
    }


class TestLeaving:
    def test_self_leave_kicks_and_tells(self, bot):
        bot._handle(event("chat_kick_user", 555))

        assert bot._vk.kicked == [(CHAT, 555)]
        assert len(bot._vk.sent) == 1
        peer, text = bot._vk.sent[0]
        assert peer == PEER
        assert "[id555|Иван Иванов] вышел из беседы" in text
        assert bot._state.has_left(555)

    def test_kick_uses_chat_number_not_peer_id(self, bot):
        # На peer_id ВКонтакте отвечает «chat_id should be less than 100000000».
        bot._handle(event("chat_kick_user", 555))
        assert bot._vk.kicked[0][0] == chat_number(PEER) == 2

    def test_kicked_by_admin_is_not_our_business(self, bot):
        # Исключённому админом и так нельзя вернуться: ни кика, ни сообщения.
        bot._handle(event("chat_kick_user", 555, author=999))

        assert bot._vk.kicked == [] and bot._vk.sent == []
        assert not bot._state.has_left(555)

    def test_communities_are_ignored(self, bot):
        bot._handle(event("chat_kick_user", -123))
        assert bot._vk.kicked == [] and bot._vk.sent == []

    def test_notice_admits_when_the_kick_failed(self, bot):
        bot._vk.kick_ok = False
        bot._handle(event("chat_kick_user", 555))

        text = bot._vk.sent[0][1]
        assert "вышел из беседы." in text
        assert "Вернуться самостоятельно уже не получится" not in text

    def test_kick_can_be_switched_off(self, bot):
        bot._cfg.leave_kick = False
        bot._handle(event("chat_kick_user", 555))

        assert bot._vk.kicked == []
        assert len(bot._vk.sent) == 1

    def test_notice_can_be_switched_off(self, bot):
        bot._cfg.leave_notify = False
        bot._handle(event("chat_kick_user", 555))

        assert bot._vk.kicked == [(CHAT, 555)]
        assert bot._vk.sent == []


class TestReturning:
    def test_sneaking_back_is_kicked_again(self, bot):
        bot._handle(event("chat_kick_user", 555))
        bot._vk.kicked.clear()
        bot._vk.sent.clear()

        # Вторая линия обороны: если ВКонтакте всё же пустил его обратно.
        bot._handle(event("chat_invite_user", 555))

        assert bot._vk.kicked == [(CHAT, 555)]
        assert "вернулся в беседу сам и был исключён" in bot._vk.sent[0][1]

    def test_invited_back_by_someone_is_welcome(self, bot):
        bot._handle(event("chat_kick_user", 555))
        bot._vk.kicked.clear()
        bot._vk.sent.clear()

        bot._handle(event("chat_invite_user", 555, author=999))

        assert bot._vk.kicked == [] and bot._vk.sent == []
        # Метка снята: следующий его выход снова обработается как первый.
        assert not bot._state.has_left(555)

    def test_a_newcomer_is_not_touched(self, bot):
        bot._handle(event("chat_invite_user", 777))
        assert bot._vk.kicked == [] and bot._vk.sent == []

    def test_by_link_counts_too(self, bot):
        bot._handle(event("chat_kick_user", 555))
        bot._vk.kicked.clear()

        bot._handle(event("chat_invite_user_by_link", 555))
        assert bot._vk.kicked == [(CHAT, 555)]


class TestMemory:
    def test_left_list_survives_restart(self, tmp_path):
        path = tmp_path / "state.json"
        first = State(path)
        first.mark_left(555, datetime(2026, 8, 19, 12, 0, tzinfo=TZ))
        first.save()

        assert State(path).has_left(555)

    def test_forget_is_forget(self, tmp_path):
        state = State(tmp_path / "state.json")
        state.mark_left(555, datetime(2026, 8, 19, 12, 0, tzinfo=TZ))
        state.forget_left(555)
        assert not state.has_left(555)

    def test_name_is_asked_once(self, bot):
        bot._handle(event("chat_kick_user", 555))
        bot._handle(event("chat_invite_user", 555))
        assert bot._names == {555: "Иван Иванов"}


class TestNotices:
    def test_mention_is_a_profile_link(self):
        assert render.mention(555, "Иван Иванов") == "[id555|Иван Иванов]"

    def test_nameless_user_still_reads(self):
        assert render.mention(555, "") == "[id555|участник]"

    def test_ordinary_messages_still_work(self, bot):
        # Охрана не должна перехватывать обычные сообщения с командами.
        bot._handle({"type": "message_new", "object": {"message": {
            "peer_id": PEER, "from_id": 555, "text": "помощь"}}})
        assert "понимаю команды" in bot._vk.sent[0][1]
