"""Offline safety checks for the optional interactive browser helper."""

import importlib.util
import json
import stat
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "eon_login", Path(__file__).parents[1] / "scripts/eon_login.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_callback_requires_exact_destination():
    assert (
        helper.parse_callback(helper.CALLBACK + "?state=ours&code=ok", "ours") == "ok"
    )
    assert (
        helper.parse_callback("https://other.example/?state=ours&code=ok", "ours")
        is None
    )
    assert (
        helper.parse_callback(helper.CALLBACK + "/other?state=ours&code=ok", "ours")
        is None
    )


@pytest.mark.parametrize(
    "query",
    [
        "state=other&code=ok",
        "state=ours&state=ours&code=ok",
        "state=ours&code=ok&code=other",
        "state=ours&code=ok&error=denied",
        "state=ours&code=",
        "code=ok",
        "state=ours&code=ok#fragment",
    ],
)
def test_rejects_invalid_callback(query):
    with pytest.raises(ValueError):
        helper.parse_callback(helper.CALLBACK + "?" + query, "ours")


def test_session_file_is_private_and_never_overwritten(tmp_path):
    path = tmp_path / "session.json"
    helper.save_session(path, "person@example.com", "session")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {
        "email": "person@example.com",
        "refresh_token": "session",
    }
    with pytest.raises(FileExistsError):
        helper.save_session(path, "other@example.com", "other-session")
    assert json.loads(path.read_text())["refresh_token"] == "session"


def test_session_file_does_not_follow_symlinks(tmp_path):
    target = tmp_path / "missing-target.json"
    path = tmp_path / "session.json"
    path.symlink_to(target)
    with pytest.raises(FileExistsError):
        helper.save_session(path, "person@example.com", "session")
    assert not target.exists()
