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

import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import MetadataValue
from audiobiblio.web.deps import get_db

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
    if not any(path.startswith(r + "/") or path == r for r in ALLOWED) \
            and path not in _near_paths():
        raise HTTPException(422, "cesta mimo povolene chaos koreny")
    d = BASE / path
    if not d.is_dir():
        raise HTTPException(404, "adresar neexistuje")
    files = sorted((f.name, f.stat().st_size) for f in d.rglob("*")
                   if f.is_file() and "@eaDir" not in str(f))
    return {"files": [{"name": n, "mb": round(s / 1e6, 1)} for n, s in files]}


# ---------------------------------------------------------------------------
# Near duplicates — similar copies reviewed by hand (2026-10-09)
# ---------------------------------------------------------------------------
# Not byte-identical (other names/formats, the user's own copy next to the
# shelved one), so nothing is ever deleted: "trash" moves the dir into the
# share's #recycle (restorable from DSM). The copy audiobiblio manages (a
# work's final_path) is refused — removing it would leave the DB pointing
# at nothing; keeping the user's copy instead is an adopt.

NEAR_JSON = Path("/media/ebooks/near_dups.json")
RECYCLE = Path("/media/ebooks/#recycle/audiobiblio-dups")
_MOUNT_TO_SHARE = {"/media/fiction/": "eBOOKs.fiction/",
                   "/media/nonfiction/": "eBOOKs.nonfiction/"}


def _near_sig(paths: list[str]) -> str:
    return hashlib.md5("\n".join(sorted(paths)).encode()).hexdigest()


def _read_near() -> list[dict]:
    if not NEAR_JSON.exists():
        return []
    return json.loads(NEAR_JSON.read_text()).get("groups", [])


def add_near_group(label: str, paths: list[str], reason: str = "") -> str:
    """Register a group of similar dirs (paths relative to the eBOOKs share).
    Idempotent: the same set of paths is stored once."""
    groups = _read_near()
    sig = _near_sig(paths)
    if not any(g["sig"] == sig for g in groups):
        groups.append({"sig": sig, "label": label, "reason": reason,
                       "dirs": [{"path": p} for p in paths]})
        NEAR_JSON.write_text(json.dumps({"groups": groups}, ensure_ascii=False, indent=1))
    return sig


def _managed_paths(db: Session) -> set[str]:
    """Shelved works' folders, as share-relative paths."""
    out = set()
    for (value,) in db.query(MetadataValue.value).filter(
            MetadataValue.entity_type == "work", MetadataValue.field == "final_path"):
        for mount, share in _MOUNT_TO_SHARE.items():
            if value and value.startswith(mount):
                out.add(share + value[len(mount):])
    return out


def _dir_stats(path: Path) -> tuple[int, int]:
    files = [f for f in path.rglob("*") if f.is_file() and "@eaDir" not in str(f)]
    return len(files), sum(f.stat().st_size for f in files)


def load_near_groups(db: Session, include_single: bool = False) -> list[dict]:
    managed = _managed_paths(db)
    out = []
    for g in _read_near():
        alive = []
        for d in g["dirs"]:
            full = BASE / d["path"]
            if full.is_dir():
                files, size = _dir_stats(full)
                alive.append({"path": d["path"], "files": files, "size": size,
                              "managed": d["path"] in managed})
        if len(alive) > 1 or (include_single and alive):
            out.append({**g, "dirs": alive})
    return out


class NearTrashRequest(BaseModel):
    sig: str
    path: str


@router.post("/near/trash")
def trash_near_copy(body: NearTrashRequest, db: Session = Depends(get_db)):
    """Move ONE copy of a near-duplicate group into #recycle."""
    group = next((g for g in load_near_groups(db, include_single=True)
                  if g["sig"] == body.sig), None)
    entry = next((d for d in (group or {}).get("dirs", []) if d["path"] == body.path), None)
    if group is None or entry is None:
        raise HTTPException(404, "kopie neni v teto skupine")
    if entry["managed"]:
        raise HTTPException(409, "tuto kopii spravuje audiobiblio (je v knihovne) — "
                                 "chcete-li ponechat jinou, pouzijte Adoptovat")
    if len(group["dirs"]) < 2:
        raise HTTPException(409, "posledni zijici kopie — presun zamitnut")
    target = RECYCLE / body.path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(BASE / body.path), str(target))
    return {"trashed": body.path, "to": str(target),
            "freed_mb": round(entry["size"] / 1e6, 1)}


def _near_paths() -> set[str]:
    return {d["path"] for g in _read_near() for d in g["dirs"]}
