"""Ownership hand-over (only meaningful as root; otherwise a safe no-op)."""
from __future__ import annotations

import os

import pytest

from audiobiblio.core import fsperm


def test_noop_when_not_root(tmp_path):
    f = tmp_path / "a.m4a"; f.write_bytes(b"x")
    if os.geteuid() == 0:
        pytest.skip("running as root")
    assert fsperm.adopt_parent_owner(f) == 0
    assert fsperm.fix_root_owned(tmp_path) == 0


def test_adopts_parent_owner_as_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fsperm.os, "geteuid", lambda: 0)
    monkeypatch.setattr(fsperm.os, "lchown", lambda p, u, g: calls.append((str(p), u, g)))
    d = tmp_path / "book"; d.mkdir(); (d / "01.m4a").write_bytes(b"x")
    st = tmp_path.stat()
    # pretend everything is owned by someone else than the parent
    real_lstat = os.lstat
    class Fake:
        def __init__(self, s): self._s = s
        def __getattr__(self, k):
            if k in ("st_uid", "st_gid"): return 4242
            return getattr(self._s, k)
    monkeypatch.setattr(fsperm.Path, "lstat", lambda self: Fake(real_lstat(self)))
    assert fsperm.adopt_parent_owner(d) == 2
    assert {c[1:] for c in calls} == {(st.st_uid, st.st_gid)}
    assert oct((d / "01.m4a").stat().st_mode & 0o777) == oct(fsperm.FILE_MODE)
