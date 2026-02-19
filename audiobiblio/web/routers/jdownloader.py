"""
routers/jdownloader — JDownloader 2 send-to and status API.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...jdownloader import JDownloaderClient
from ...config import load_config

router = APIRouter(prefix="/api/v1/jdownloader", tags=["jdownloader"])


def _client() -> JDownloaderClient:
    cfg = load_config()
    return JDownloaderClient(host=cfg.jd_host, port=cfg.jd_port)


class AddLinksRequest(BaseModel):
    urls: list[str]
    package_name: str | None = None
    dest_folder: str | None = None


class AddLinksResponse(BaseModel):
    ok: bool
    detail: str = ""


class JDStatusResponse(BaseModel):
    available: bool
    packages: list[dict] = []


@router.post("/add", response_model=AddLinksResponse)
def add_links(req: AddLinksRequest):
    if not req.urls:
        raise HTTPException(400, "No URLs provided")
    client = _client()
    try:
        client.add_links(
            urls=req.urls,
            package_name=req.package_name,
            dest_folder=req.dest_folder,
        )
        return AddLinksResponse(ok=True, detail=f"Added {len(req.urls)} link(s)")
    except Exception as e:
        return AddLinksResponse(ok=False, detail=str(e))


@router.get("/status", response_model=JDStatusResponse)
def jd_status():
    client = _client()
    available = client.is_available()
    packages = []
    if available:
        try:
            packages = client.query_packages()
        except Exception:
            pass
    return JDStatusResponse(available=available, packages=packages)
