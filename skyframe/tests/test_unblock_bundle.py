"""desktop.unblock_bundle removes the downloaded-from-internet mark from the
bundled .NET / native libraries (pythonnet refuses blocked DLLs)."""

import os
import sys

from skyframe import desktop


def test_noop_when_not_frozen_windows(tmp_path):
    assert desktop.unblock_bundle(str(tmp_path)) == 0


def test_targets_zone_identifier_streams(tmp_path, monkeypatch):
    (tmp_path / "pythonnet" / "runtime").mkdir(parents=True)
    (tmp_path / "pythonnet" / "runtime" / "Python.Runtime.dll").write_bytes(b"x")
    (tmp_path / "webview" / "lib").mkdir(parents=True)
    (tmp_path / "webview" / "lib" / "WebView2Loader.dll").write_bytes(b"x")
    (tmp_path / "python312.dll").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("x")
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    removed = []
    monkeypatch.setattr(desktop.os, "remove", lambda p: removed.append(p))
    n = desktop.unblock_bundle(str(tmp_path))
    assert n == 3
    assert all(p.endswith(":Zone.Identifier") for p in removed)
    assert any("Python.Runtime.dll" in p for p in removed)
    assert not any("readme.txt" in p for p in removed)
