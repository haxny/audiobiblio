"""/dup-review page and API (decisions recorded, listing side by side)."""
from __future__ import annotations

import pytest

from audiobiblio.core.db.models import DupCandidate
from audiobiblio.library import dup_review as dr


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    base = tmp_path / "ebooks"
    monkeypatch.setattr(dr, "BASE", base)
    monkeypatch.setattr(dr, "RECYCLE", base / "#recycle" / "audiobiblio-dups")
    return base


def _cand(db, roots, temp_sizes, shelf_sizes, name):
    dirs = []
    for d, sizes in ((roots / "eBOOKs.temp" / name, temp_sizes),
                     (roots / "eBOOKs.fiction" / name, shelf_sizes)):
        d.mkdir(parents=True)
        for i, n in enumerate(sizes, 1):
            (d / f"{i:02d}.mp3").write_bytes(b"x" * n)
        dirs.append(d)
    dr.import_rows(db, [{"temp": str(dirs[0]), "shelf": str(dirs[1]), "MB_temp": 1,
                         "files_temp": len(temp_sizes), "MB_shelf": 1,
                         "files_shelf": len(shelf_sizes), "min_temp": "60"}])


def test_page_renders_groups(db_session, roots):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from audiobiblio.web.deps import get_db
    from audiobiblio.web.routers import dup_review as router_mod
    from audiobiblio.web.views import router as views_router
    _cand(db_session, roots, [10, 20], [10, 20], name="A")
    _cand(db_session, roots, [10, 20], [10, 99], name="B")
    app = FastAPI(); app.include_router(views_router); app.include_router(router_mod.router)
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    html = client.get("/dup-review").text
    assert "Návrh systému" in html and "K rozhodnutí" in html
    c = db_session.query(DupCandidate).filter_by(verdict="shelf_bigger").one()
    assert client.post("/api/v1/dup-review/decide", json={"ids": [c.id], "decision": "keep_both"}).json() == {"updated": 1}
    assert client.post("/api/v1/dup-review/decide", json={"ids": [c.id], "decision": "nuke"}).status_code == 422
    listing = client.get(f"/api/v1/dup-review/{c.id}/listing").json()
    assert [f["name"] for f in listing["shelf"]] == ["01.mp3", "02.mp3"]
