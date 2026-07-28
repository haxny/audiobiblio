"""routers/chaos — duplicate-directory review & one-by-one resolution.

The scan (host-side) writes /media/ebooks/chaos_dups.json; this router
shows the groups and deletes ONE copy at a time, only after re-verifying
the directory is still an exact duplicate (names+sizes) of a surviving
sibling. Nothing is ever deleted in bulk or without the user's click.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/chaos", tags=["chaos"])

DUPS_JSON = Path("/media/ebooks/chaos_dups.json")
BASE = Path("/media/ebooks")
ALLOWED = ("eBOOKs.downloads", "eBOOKs.INCOMPLETE", "eBOOKs.temp",
           "eBOOKs.temp2sort", "eBOOKs.temp2sort.ZV", "eBOOKs.temp2sort2025",
           "eBOOKs.Zlín", "mujrozhlas", "x")


def load_groups() -> list[dict]:
    if not DUPS_JSON.exists():
        return []
    groups = json.loads(DUPS_JSON.read_text()).get("groups", [])
    # drop dirs that no longer exist; drop groups reduced to <2 dirs
    out = []
    for g in groups:
        alive = [d for d in g["dirs"] if (BASE / d["path"]).is_dir()]
        if len(alive) > 1:
            out.append({**g, "dirs": alive,
                        "save": alive[0]["size"] * (len(alive) - 1)})
    return out


def _sig_of(path: Path) -> set:
    out = set()
    for f in path.rglob("*"):
        if f.is_file() and "@eaDir" not in str(f):
            out.add((f.name, f.stat().st_size))
    return out


class DupDeleteRequest(BaseModel):
    sig: str
    path: str


@router.post("/dups/delete")
def delete_dup_copy(body: DupDeleteRequest):
    """Delete ONE verified duplicate copy. Refuses when the dir is no longer
    an exact match of a surviving sibling (belt & braces re-check)."""
    if not any(body.path.startswith(r + "/") or body.path == r for r in ALLOWED):
        raise HTTPException(422, "cesta mimo povolene chaos koreny")
    target = BASE / body.path
    if not target.is_dir():
        raise HTTPException(404, "adresar neexistuje")
    groups = json.loads(DUPS_JSON.read_text()).get("groups", [])
    group = next((g for g in groups if g["sig"] == body.sig), None)
    if group is None:
        raise HTTPException(404, "skupina nenalezena")
    siblings = [d for d in group["dirs"] if d["path"] != body.path
                and (BASE / d["path"]).is_dir()]
    if not siblings:
        raise HTTPException(409, "zadna zijici druha kopie — mazani zamitnuto")
    t_sig = _sig_of(target)
    if not any(_sig_of(BASE / s["path"]) == t_sig for s in siblings):
        raise HTTPException(409,
            "obsah uz neni exaktni duplikat prezivajici kopie — mazani zamitnuto")
    freed = sum(sz for _, sz in t_sig)
    shutil.rmtree(target)
    return {"deleted": body.path, "freed_mb": round(freed / 1e6, 1)}


@router.get("/dups/listing")
def dup_listing(path: str):
    """File listing of one dir (review aid)."""
    if not any(path.startswith(r + "/") or path == r for r in ALLOWED):
        raise HTTPException(422, "cesta mimo povolene chaos koreny")
    d = BASE / path
    if not d.is_dir():
        raise HTTPException(404, "adresar neexistuje")
    files = sorted((f.name, f.stat().st_size) for f in d.rglob("*")
                   if f.is_file() and "@eaDir" not in str(f))
    return {"files": [{"name": n, "mb": round(s / 1e6, 1)} for n, s in files]}
