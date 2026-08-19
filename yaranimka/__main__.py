"""Точка входа: python -m yaranimka [run|digest|today|tomorrow|week]."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

from .bot import Bot
from .config import Config
from .shikimori import ShikimoriError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yaranimka", description="Чат-бот сообщества ВКонтакте: онгоинги дня")
    parser.add_argument(
        "mode",
        nargs="?",
        default="run",
        choices=("run", "digest", "check", "today", "tomorrow", "week", "releases"),
        help="run — слушать беседу, слать дайджест и следить за раздачами; "
             "digest — отправить дайджест за сегодня и выйти; "
             "check — один обход торрентов и выйти; "
             "today/tomorrow/week — показать текст в консоли, ничего не отправляя; "
             "releases — показать список слежения",
    )
    parser.add_argument("--dry-run", action="store_true", help="ничего не отправлять, только логи")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = Config()
    if args.dry_run:
        cfg.dry_run = True

    # Печать в консоль токена не требует — конфиг проверяем только для отправки.
    if args.mode in ("run", "digest", "check"):
        cfg.check()

    try:
        return _run(args, cfg)
    except ShikimoriError as exc:
        # Разовый режим завершается по-человечески: трейсбек здесь ничего
        # не объясняет, а в планировщике только засоряет лог.
        print(f"Не получилось: {exc}", file=sys.stderr)
        return 1


def _run(args, cfg: Config) -> int:
    with Bot(cfg) as bot:
        if args.mode == "run":
            bot.run()
        elif args.mode == "digest":
            bot.send_digest()
        elif args.mode == "check":
            print(f"Новых раздач: {bot.check_releases()}")
        elif args.mode == "today":
            print(bot.digest())
        elif args.mode == "tomorrow":
            print(bot.digest(bot.now().date() + timedelta(days=1)))
        elif args.mode == "week":
            print(bot.answer("week", ""))
        elif args.mode == "releases":
            print(bot.answer("releases", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
