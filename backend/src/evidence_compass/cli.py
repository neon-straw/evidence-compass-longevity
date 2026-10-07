"""Служебные команды установки. Регистрации нет: аккаунт создаётся здесь (ADR 0008)."""

import argparse
import asyncio
import getpass
import sys

from evidence_compass import accounts
from evidence_compass.db import create_engine, create_sessionmaker
from evidence_compass.settings import Settings


async def _create_account(login: str, password: str) -> None:
    engine = create_engine(Settings())
    try:
        async with create_sessionmaker(engine)() as db:
            await accounts.create_account(db, login, password)
    finally:
        await engine.dispose()


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("Пароль: ")
    if password != getpass.getpass("Повторите пароль: "):
        sys.exit("Пароли не совпадают")
    return password


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="evidence_compass.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-account", help="создать аккаунт для входа")
    create.add_argument("login")
    create.add_argument("--password-stdin", action="store_true", help="прочитать пароль из stdin")
    args = parser.parse_args(argv)

    password = _read_password(args.password_stdin)
    if not password:
        sys.exit("Пароль не может быть пустым")
    try:
        asyncio.run(_create_account(args.login, password))
    except accounts.AccountExistsError:
        sys.exit(f"Аккаунт «{args.login}» уже существует")
    print(f"Аккаунт «{args.login}» создан")


if __name__ == "__main__":
    main()
