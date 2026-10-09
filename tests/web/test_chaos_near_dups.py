"""Near-duplicate review: similar (not identical) copies, trash = reversible.

The exact-duplicate tool only deletes byte-identical copies inside the
temp roots. Near duplicates (other names/formats, the user's own copy vs
the shelved one) are reviewed by hand and go to the share's #recycle.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audiobiblio.core.db.models import FieldOrigin, MetadataValue
from audiobiblio.web.deps import get_db
from audiobiblio.web.routers import chaos

MINE = "eBOOKs.fiction/Vit Vencl [audio]/Vencl Vit - Chvilka stesti CRo 2022(52m)"
OURS = "eBOOKs.fiction/Vit Vencl [audio]/Vit Vencl - (2022) Chvilka stesti (cte X, CRo 2022)"


@pytest.fixture()
def env(tmp_path, monkeypatch, db_session):
    base = tmp_path / "ebooks"
    for rel in (MINE, OURS):
        d = base / rel
        d.mkdir(parents=True)
        (d / "a.m4a").write_bytes(b"x" * 100)
    monkeypatch.setattr(chaos, "BASE", base)
    monkeypatch.setattr(chaos, "NEAR_JSON", base / "near_dups.json")
    monkeypatch.setattr(chaos, "RECYCLE", base / "#recycle" / "audiobiblio-dups")
    db_session.add(MetadataValue(entity_type="work", entity_id=1, field="final_path",
                                 value="/media/fiction/" + OURS.split("/", 1)[1],
                                 origin=FieldOrigin.MANUAL, source="t"))
    db_session.commit()
    chaos.add_near_group("Chvilka štěstí (Vít Vencl)", [MINE, OURS],
                         reason="shelved copy duplicates the user's copies")
    app = FastAPI()
    app.include_router(chaos.router)
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    return base, TestClient(app), db_session


def test_groups_list_marks_the_managed_copy(env):
    base, _, db_session = env
    groups = chaos.load_near_groups(db_session)
    assert len(groups) == 1 and groups[0]["label"] == "Chvilka štěstí (Vít Vencl)"
    managed = {d["path"]: d["managed"] for d in groups[0]["dirs"]}
    assert managed == {MINE: False, OURS: True}


def test_adding_the_same_group_twice_does_not_duplicate(env):
    _, _, db_session = env
    chaos.add_near_group("Chvilka štěstí (Vít Vencl)", [OURS, MINE])
    assert len(json.loads(chaos.NEAR_JSON.read_text())["groups"]) == 1


def test_trash_moves_users_copy_to_recycle(env):
    base, client, db_session = env
    sig = chaos.load_near_groups(db_session)[0]["sig"]
    r = client.post("/api/v1/chaos/near/trash", json={"sig": sig, "path": MINE})
    assert r.status_code == 200, r.text
    assert not (base / MINE).exists()
    assert (chaos.RECYCLE / MINE / "a.m4a").exists()


def test_managed_copy_is_refused(env):
    _, client, db_session = env
    sig = chaos.load_near_groups(db_session)[0]["sig"]
    r = client.post("/api/v1/chaos/near/trash", json={"sig": sig, "path": OURS})
    assert r.status_code == 409 and "audiobiblio" in r.json()["detail"]


def test_last_surviving_copy_is_refused(env):
    base, client, db_session = env
    sig = chaos.load_near_groups(db_session)[0]["sig"]
    client.post("/api/v1/chaos/near/trash", json={"sig": sig, "path": MINE})
    # a group whose other copy is gone: the remaining one is the last copy
    other = MINE.replace("Vencl Vit", "Other")
    gone = MINE.replace("Vencl Vit", "Gone")
    (base / other).mkdir(parents=True)
    chaos.add_near_group("solo", [other, gone])
    solo = next(g for g in chaos.load_near_groups(db_session, include_single=True)
                if g["label"] == "solo")
    r = client.post("/api/v1/chaos/near/trash", json={"sig": solo["sig"], "path": other})
    assert r.status_code == 409


def test_path_outside_group_is_refused(env):
    _, client, db_session = env
    sig = chaos.load_near_groups(db_session)[0]["sig"]
    r = client.post("/api/v1/chaos/near/trash", json={"sig": sig, "path": "eBOOKs.fiction/x"})
    assert r.status_code == 404
