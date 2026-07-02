"""
jdownloader — REST client for JDownloader 2 headless (local API, port 3129).

JD2 runs as a separate Docker container on the same network.
Used as a download backend for rozhlas.cz URLs (where yt-dlp's MujRozhlas
extractor doesn't apply).
"""
from __future__ import annotations
import os
import time
import structlog
import requests

log = structlog.get_logger()

DEFAULT_HOST = os.environ.get("JD_HOST", "localhost")
DEFAULT_PORT = int(os.environ.get("JD_PORT", "3129"))


class JDownloaderClient:
    """Minimal REST client for JDownloader 2's local event API."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.base = f"http://{host}:{port}"
        self.session = requests.Session()

    def _post(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base}{endpoint}"
        r = self.session.post(url, json=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _get(self, endpoint: str) -> dict:
        url = f"{self.base}{endpoint}"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.json()

    def add_links(self, urls: list[str], package_name: str | None = None,
                  dest_folder: str | None = None) -> dict:
        """Add links to the link grabber."""
        params = {
            "autostart": True,
            "links": "\n".join(urls),
        }
        if package_name:
            params["packageName"] = package_name
        if dest_folder:
            params["destinationFolder"] = dest_folder
        return self._post("/linkgrabberv2/addLinks", params)

    def query_packages(self) -> list[dict]:
        """Query current download packages."""
        return self._post("/downloadsV2/queryPackages", {
            "bytesLoaded": True,
            "bytesTotal": True,
            "status": True,
            "finished": True,
            "running": True,
            "enabled": True,
        })

    def query_links(self, package_uuids: list[int] | None = None) -> list[dict]:
        """Query links in packages."""
        params = {
            "bytesLoaded": True,
            "bytesTotal": True,
            "status": True,
            "finished": True,
            "running": True,
            "url": True,
        }
        if package_uuids:
            params["packageUUIDs"] = package_uuids
        return self._post("/downloadsV2/queryLinks", params)

    def is_available(self) -> bool:
        """Check if JD2 API is reachable."""
        try:
            self._get("/jd/version")
            return True
        except Exception:
            return False


def select_backend(url: str) -> str:
    """
    Choose download backend based on URL.
    - mujrozhlas.cz → yt-dlp (MujRozhlas extractor works)
    - rozhlas.cz (non-mujrozhlas) → jdownloader
    - other → yt-dlp (generic)
    """
    from urllib.parse import urlparse
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "yt-dlp"

    if "mujrozhlas.cz" in host:
        return "yt-dlp"
    if "rozhlas.cz" in host:
        return "jdownloader"
    return "yt-dlp"
