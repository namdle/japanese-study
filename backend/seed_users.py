"""Profile management CLI.

Usage examples (from the backend/ directory, with the venv active):

    python seed_users.py list
    python seed_users.py create "Mom" --admin
    python seed_users.py create "Kid1"
    python seed_users.py rename Kid1 "Sora"
    python seed_users.py set-admin Mom --on
    python seed_users.py set-admin Mom --off
    python seed_users.py delete Kid2

The script uses the same SQLAlchemy engine as the running app, so
APP_DATA_DIR / database location are honored via app.config.Settings.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db import create_app_engine, init_db, users_table


def _print_users(rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        print("(no profiles)")
        return
    print(f"{'id':>3}  {'name':<20}  admin  level  voice")
    print("-" * 50)
    for r in rows:
        flag = "yes" if r["is_admin"] else "no"
        print(f"{r['id']:>3}  {r['name']:<20}  {flag:<5}  {r['level']:<5}  {r['voice']}")


def _resolve_user(engine: Engine, identifier: str) -> Mapping[str, object]:
    """Resolve a user by numeric id or by exact name."""
    with engine.connect() as conn:
        row = None
        if identifier.isdigit():
            row = conn.execute(
                select(users_table).where(users_table.c.id == int(identifier))
            ).mappings().one_or_none()
        if row is None:
            row = conn.execute(
                select(users_table).where(users_table.c.name == identifier)
            ).mappings().one_or_none()
    if row is None:
        print(f"error: no profile matching {identifier!r}", file=sys.stderr)
        sys.exit(2)
    return row


def cmd_list(engine: Engine, _args: argparse.Namespace) -> int:
    with engine.connect() as conn:
        rows = conn.execute(
            select(users_table).order_by(users_table.c.id)
        ).mappings().all()
    _print_users(rows)
    return 0


def cmd_create(engine: Engine, args: argparse.Namespace) -> int:
    name: str = args.name.strip()
    if not name:
        print("error: name is required", file=sys.stderr)
        return 2
    try:
        with engine.begin() as conn:
            result = conn.execute(
                insert(users_table).values(name=name, is_admin=1 if args.admin else 0)
            )
            new_id = result.inserted_primary_key[0]
            row = conn.execute(
                select(users_table).where(users_table.c.id == new_id)
            ).mappings().one()
    except IntegrityError:
        print(f"error: name {name!r} already taken", file=sys.stderr)
        return 1
    admin_label = "yes" if row["is_admin"] else "no"
    print(f"created profile id={row['id']} name={row['name']!r} admin={admin_label}")
    return 0


def cmd_rename(engine: Engine, args: argparse.Namespace) -> int:
    user = _resolve_user(engine, args.identifier)
    new_name = args.new_name.strip()
    if not new_name:
        print("error: new name is required", file=sys.stderr)
        return 2
    try:
        with engine.begin() as conn:
            conn.execute(
                update(users_table)
                .where(users_table.c.id == user["id"])
                .values(name=new_name)
            )
    except IntegrityError:
        print(f"error: name {new_name!r} already taken", file=sys.stderr)
        return 1
    print(f"renamed id={user['id']}: {user['name']!r} -> {new_name!r}")
    return 0


def cmd_set_admin(engine: Engine, args: argparse.Namespace) -> int:
    user = _resolve_user(engine, args.identifier)
    new_value = 1 if args.on else 0
    with engine.begin() as conn:
        conn.execute(
            update(users_table)
            .where(users_table.c.id == user["id"])
            .values(is_admin=new_value)
        )
    label = "admin" if new_value else "not admin"
    print(f"set id={user['id']} ({user['name']!r}) -> {label}")
    return 0


def cmd_delete(engine: Engine, args: argparse.Namespace) -> int:
    user = _resolve_user(engine, args.identifier)
    with engine.begin() as conn:
        conn.execute(delete(users_table).where(users_table.c.id == user["id"]))
    print(f"deleted id={user['id']} ({user['name']!r})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage profiles for the Japanese Study app.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all profiles")

    p_create = sub.add_parser("create", help="Create a profile")
    p_create.add_argument("name", help="Profile name (must be unique)")
    p_create.add_argument("--admin", action="store_true", help="Mark this profile as an admin")

    p_rename = sub.add_parser("rename", help="Rename a profile")
    p_rename.add_argument("identifier", help="Profile id or current name")
    p_rename.add_argument("new_name", help="New profile name")

    p_set_admin = sub.add_parser("set-admin", help="Toggle admin flag on a profile")
    p_set_admin.add_argument("identifier", help="Profile id or name")
    grp = p_set_admin.add_mutually_exclusive_group(required=True)
    grp.add_argument("--on", action="store_true", help="Mark as admin")
    grp.add_argument("--off", action="store_true", help="Remove admin")

    p_delete = sub.add_parser("delete", help="Delete a profile")
    p_delete.add_argument("identifier", help="Profile id or name")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    engine = create_app_engine()
    init_db(engine)

    handlers = {
        "list": cmd_list,
        "create": cmd_create,
        "rename": cmd_rename,
        "set-admin": cmd_set_admin,
        "delete": cmd_delete,
    }
    handler = handlers[args.cmd]
    return handler(engine, args)


if __name__ == "__main__":
    raise SystemExit(main())
