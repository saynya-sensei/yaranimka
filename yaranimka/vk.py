"""Клиент VK API и Bots Long Poll: приём событий и ответы от лица сообщества."""

from __future__ import annotations

import logging
import random
import time

import httpx

log = logging.getLogger(__name__)

API = "https://api.vk.com/method"
VERSION = "5.199"

# Больше 4096 символов ВКонтакте не принимает, режем с запасом на «…».
MAX_LEN = 4000

# Токен сообщества ограничен тремя запросами в секунду. Ошибка 6 — это «слишком
# часто», её достаточно переждать; 9 — флуд-контроль, он снимается дольше.
RETRY_CODES = {6, 9, 10}


class VKError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"VK API {code}: {message}")
        self.code = code


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Длинное сообщение — на части по границам строк."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    chunk = ""

    def flush() -> None:
        nonlocal chunk
        if chunk.strip():
            parts.append(chunk.rstrip("\n"))
        chunk = ""

    for line in text.split("\n"):
        # Одна строка длиннее лимита переносу не поддаётся — режем как есть.
        while len(line) > limit:
            flush()
            parts.append(line[:limit])
            line = line[limit:]
        if len(chunk) + len(line) + 1 > limit:
            flush()
        chunk += line + "\n"

    flush()
    return parts


class VKClient:
    def __init__(self, token: str, *, timeout: float = 40.0, dry_run: bool = False):
        self._token = token
        self._dry_run = dry_run
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "VKClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def http(self) -> httpx.Client:
        """Тот же клиент отдаём Long Poll: он ходит на свой сервер, не в API."""
        return self._client

    def call(self, method: str, **params) -> dict | list:
        payload = {k: v for k, v in params.items() if v is not None}
        payload.update(access_token=self._token, v=VERSION)

        for attempt in range(4):
            try:
                resp = self._client.post(f"{API}/{method}", data=payload)
            except httpx.HTTPError as exc:
                log.warning("Сеть подвела на %s: %s", method, exc)
                time.sleep(2**attempt)
                continue

            if resp.status_code >= 500:
                time.sleep(2**attempt)
                continue

            data = resp.json()
            if "response" in data:
                return data["response"]

            error = data.get("error", {})
            code = int(error.get("error_code", 0))
            message = error.get("error_msg", resp.text)
            if code in RETRY_CODES:
                wait = 2**attempt
                log.warning("VK просит сбавить темп (%s), ждём %s с", code, wait)
                time.sleep(wait)
                continue
            raise VKError(code, message)

        raise VKError(0, f"{method}: не ответил после нескольких попыток")

    def send_message(self, peer_id: int, text: str) -> None:
        """Отправка от лица сообщества — токен сообщества иначе и не умеет."""
        for part in split_message(text):
            if self._dry_run:
                log.info("[dry-run] в %s:\n%s", peer_id, part)
                continue
            self.call(
                "messages.send",
                peer_id=peer_id,
                message=part,
                random_id=random.getrandbits(31),
                disable_mentions=1,
                dont_parse_links=0,
            )
            time.sleep(0.4)

    def long_poll_server(self, group_id: int) -> dict:
        return self.call("groups.getLongPollServer", group_id=group_id)  # type: ignore[return-value]


class LongPoll:
    """Bots Long Poll: висим на запросе и получаем события беседы.

    Сервер и ключ живут не вечно — на failed 2 и 3 их надо перезапросить,
    иначе бот замолкает молча и навсегда.
    """

    def __init__(self, vk: VKClient, group_id: int, *, wait: int = 25):
        self._vk = vk
        self._group_id = group_id
        self._wait = wait
        self._server = ""
        self._key = ""
        self._ts = ""

    def _connect(self) -> None:
        data = self._vk.long_poll_server(self._group_id)
        self._server, self._key, self._ts = data["server"], data["key"], data["ts"]
        log.info("Long Poll подключён, ts=%s", self._ts)

    def check(self) -> list[dict]:
        """Одна итерация опроса. Возвращает события, возможно пустой список."""
        if not self._server:
            self._connect()

        try:
            resp = self._vk.http.get(
                self._server,
                params={"act": "a_check", "key": self._key, "ts": self._ts, "wait": self._wait},
                timeout=self._wait + 15,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Long Poll оборвался: %s", exc)
            time.sleep(3)
            return []

        failed = data.get("failed")
        if failed == 1:
            self._ts = data["ts"]
            return []
        if failed in (2, 3):
            self._connect()
            return []

        self._ts = data.get("ts", self._ts)
        return data.get("updates", [])
