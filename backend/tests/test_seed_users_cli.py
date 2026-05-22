"""Tests for the seed_users.py CLI."""

from __future__ import annotations

import seed_users
from app.config import Settings


def test_create_and_list(settings: Settings, capsys) -> None:  # noqa: ARG001
    assert seed_users.main(["create", "Mom", "--admin"]) == 0
    assert seed_users.main(["create", "Kid1"]) == 0
    capsys.readouterr()  # drain
    assert seed_users.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Mom" in out and "Kid1" in out
    assert "yes" in out  # Mom is admin


def test_duplicate_name_is_an_error(settings: Settings, capsys) -> None:  # noqa: ARG001
    assert seed_users.main(["create", "Mom"]) == 0
    assert seed_users.main(["create", "Mom"]) == 1
    err = capsys.readouterr().err
    assert "already taken" in err.lower()


def test_rename(settings: Settings, capsys) -> None:  # noqa: ARG001
    seed_users.main(["create", "Kid"])
    assert seed_users.main(["rename", "Kid", "Sora"]) == 0
    out = capsys.readouterr().out
    assert "Sora" in out
    assert seed_users.main(["list"]) == 0
    assert "Sora" in capsys.readouterr().out


def test_set_admin_on_and_off(settings: Settings, capsys) -> None:  # noqa: ARG001
    seed_users.main(["create", "Mom"])
    capsys.readouterr()
    assert seed_users.main(["set-admin", "Mom", "--on"]) == 0
    assert "-> admin" in capsys.readouterr().out
    assert seed_users.main(["set-admin", "Mom", "--off"]) == 0
    assert "-> not admin" in capsys.readouterr().out


def test_delete_by_name_and_id(settings: Settings, capsys) -> None:  # noqa: ARG001
    seed_users.main(["create", "Kid1"])
    seed_users.main(["create", "Kid2"])
    capsys.readouterr()
    assert seed_users.main(["delete", "Kid1"]) == 0
    assert "Kid1" in capsys.readouterr().out
    # Resolve the remaining user's id by listing.
    assert seed_users.main(["list"]) == 0
    list_out = capsys.readouterr().out
    # Format: "  1  Kid2 ..."
    [line] = [ln for ln in list_out.splitlines() if "Kid2" in ln]
    user_id = line.strip().split()[0]
    assert seed_users.main(["delete", user_id]) == 0


def test_resolve_unknown_user_exits_2(settings: Settings) -> None:  # noqa: ARG001
    try:
        seed_users.main(["rename", "ghost", "x"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected SystemExit")
