from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import json, subprocess, shutil, sys, re, requests

_MRZ_CLEAN_RE = re.compile(r"\s+")

def _yt_cmd() -> list[str]:
    exe = shutil.which("yt-dlp") or shutil.which("yt_dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        raise RuntimeError("yt-dlp is not installed/importable")

def _prefer_page_url(e: dict, base_url: str | None = None) -> str:
    """
    Prefer a human page URL. Ensure it's absolute and on mujrozhlas.cz when possible.
    """
    cand = e.get("webpage_url") or e.get("original_url") or e.get("url") or ""
    if base_url and cand and not cand.startswith("http"):
        cand = urljoin(base_url, cand)
    return cand

def _clean(s: str | None) -> str | None:
    if not s:
        return s
    return _MRZ_CLEAN_RE.sub(" ", s).strip()

def probe_url(url: str) -> dict[str, Any]:
    # Flat playlist: don't resolve every child deeply (faster)
    cmd = _yt_cmd() + ["--flat-playlist", "-J", url]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "yt-dlp probe failed")
    return json.loads(p.stdout)

def deep_probe_kind(url: str) -> str:
    depth = _mrz_depth(url)
    if _is_mrz(url) and depth == 1:
        return "program"
    data = probe_url(url)
    title = _clean(data.get("title"))
    series = _clean(data.get("series") or data.get("playlist_title"))
    has_entries = isinstance(data.get("entries"), list)
    if _is_mrz(url) and depth >= 2:
        if has_entries or _looks_serial_title(title) or _looks_serial_title(series):
            return "series"
        return "episode"
    return "series" if has_entries else "episode"

def _is_mrz(u: str) -> bool:
    try:
        return urlparse(u).netloc.endswith("mujrozhlas.cz")
    except Exception:
        return False

def _mrz_parts(u: str) -> list[str]:
    if not _is_mrz(u):
        return []
    p = urlparse(u)
    path = p.path.strip("/")
    return [seg for seg in path.split("/") if seg]

def _mrz_depth(u: str) -> int:
    return len(_mrz_parts(u))

def _looks_serial_title(title: str | None) -> bool:
    if not title:
        return False
    t = title.lower()
    return "seriál" in t or "serial" in t

# absolute URL normalizer (kills trailing slashes)
def _abs_norm(base: str, href: str) -> str:
    u = urljoin(base, href)
    return u[:-1] if u.endswith("/") else u

# Match any mujrozhlas program path: /program-slug/content-slug (exactly 2 segments)
MRZ_CHILD_RE = re.compile(r"^/[a-z0-9\-]+/[a-z0-9\-]+$", re.IGNORECASE)

def mrz_discover_children(url: str) -> list[tuple[str, str]]:
    """
    On a mujrozhlas PROGRAM page like /hajaja, parse HTML and return unique
    child subpages under the same program, e.g. /hajaja/<slug> (depth==2).
    Filters out the program root, pagination, tags, etc.
    Returns [(absolute_url, title), ...] in page order.
    """
    if not _is_mrz(url) or _mrz_depth(url) != 1:
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; audiobiblio/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
    r.raise_for_status()
    if "text/html" not in r.headers.get("Content-Type", ""):
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    root = _mrz_parts(url)[0]  # e.g. "hajaja"
    prog_root_norm = _abs_norm(base, f"/{root}")

    # Only keep /<root>/<slug> (exactly two segments) and drop known noise
    urls: list[str] = []
    titles: dict[str, str] = {}  # url -> title (prefer heading near the link)

    def best_title(a_tag) -> str:
        # Try a nearby heading (article/section card)
        for parent in a_tag.parents:
            name = getattr(parent, "name", None)
            if name in ("article", "li", "div", "section"):
                h = parent.find(["h1", "h2", "h3", "h4"])
                if h and h.get_text(strip=True):
                    return _clean(h.get_text(" ").strip()) or ""
        # Fallback to link text
        return _clean(a_tag.get_text(" ").strip()) or ""

    # 1) target typical card links
    candidates = soup.select('a[href^="/'+root+'/"]')
    # 2) plus any anchors (safety net)
    candidates += soup.find_all("a", href=True)

    seen = set()
    for a in candidates:
        href = a.get("href", "").strip()
        if not href:
            continue
        absu = _abs_norm(base, href)
        parts = _mrz_parts(absu)

        # Must be depth==2 and same program root
        if len(parts) != 2 or parts[0] != root:
            continue

        # Exclude self root, pagination, tags/categories, queries/fragments
        if absu == prog_root_norm:
            continue
        if "?" in absu or "#" in absu:
            continue
        if any(seg in parts for seg in ("tag", "stitky", "tema", "temata", "rubrika", "kategorie")):
            continue
        # Regex guard (keeps only /root/<slug> exactly)
        if not MRZ_CHILD_RE.match(urlparse(absu).path):
            continue

        if absu in seen:
            continue
        seen.add(absu)

        title = best_title(a) or absu
        urls.append(absu)
        titles[absu] = title

    # preserve order and return
    return [(u, titles[u]) for u in urls]

def mrz_discover_children_depth(url: str, want_depth: int) -> list[tuple[str, str]]:
    """
    Return unique child links that are exactly `want_depth` segments deep.
    Example: for /hajaja (depth=1), want_depth=2 => /hajaja/<slug>
             for /hajaja/sny-... (depth=2), want_depth=3 => /hajaja/sny-.../<episode>
    """
    if not _is_mrz(url):
        return []
    base_p = urlparse(url)
    base = f"{base_p.scheme}://{base_p.netloc}"
    parts_root = _mrz_parts(url)
    if not parts_root:
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; audiobiblio/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
    r.raise_for_status()
    if "text/html" not in r.headers.get("Content-Type", ""):
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    def best_title(a_tag) -> str:
        for parent in a_tag.parents:
            if getattr(parent, "name", None) in ("article", "li", "div", "section"):
                h = parent.find(["h1", "h2", "h3", "h4"])
                if h and h.get_text(strip=True):
                    return _clean(h.get_text(" ").strip()) or ""
        return _clean(a_tag.get_text(" ").strip()) or ""

    seen = set()
    out: list[tuple[str, str]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        absu = _abs_norm(base, href)
        p = urlparse(absu)
        if p.netloc != base_p.netloc:
            continue
        segs = _mrz_parts(absu)

        # must start with the root segments and be exactly want_depth long
        if not segs or len(segs) != want_depth:
            continue
        if not segs[:len(parts_root)] == parts_root:
            continue

        # drop queries, fragments, tags, pagination, and self
        if p.query or p.fragment:
            continue
        if absu.rstrip("/") == url.rstrip("/"):
            continue
        if any(x in segs for x in ("tag", "stitky", "tema", "temata", "rubrika", "kategorie")):
            continue

        if absu in seen:
            continue
        seen.add(absu)

        title = best_title(a) or absu
        out.append((absu, title))

    return out

@dataclass
class EpisodeItem:
    url: str
    title: str
    episode_number: Optional[int] = None
    series: Optional[str] = None
    author: Optional[str] = None
    uploader: Optional[str] = None
    extractor: Optional[str] = None
    original: dict[str, Any] = field(default_factory=dict)

@dataclass
class ProbeResult:
    kind: str  # "episode" | "playlist" | "page"
    url: str
    title: Optional[str]
    series: Optional[str]
    uploader: Optional[str]
    extractor: Optional[str]
    entries: List[EpisodeItem] = field(default_factory=list)

def classify_probe(data: dict[str, Any], url: str) -> ProbeResult:
    extractor = data.get("extractor_key") or data.get("extractor")
    uploader = _clean(data.get("uploader"))
    title = _clean(data.get("title"))
    series = _clean(data.get("series")) or _clean(data.get("playlist_title"))

    depth = _mrz_depth(url)
    has_entries = isinstance(data.get("entries"), list)
    mrz_kind = None
    if _is_mrz(url):
        if depth == 1:
            mrz_kind = "program"  # force program
        elif depth >= 2:
            if has_entries or _looks_serial_title(title) or _looks_serial_title(series):
                mrz_kind = "series"
            else:
                mrz_kind = "episode"

    # If it has 'entries', yt-dlp treats it as a playlist/container
    # playlist/container:
    if isinstance(data.get("entries"), list):
        items = []
        for e in data["entries"]:
            if not isinstance(e, dict):
                continue
            ei = EpisodeItem(
                url=_prefer_page_url(e, base_url=url),
                title=_clean(e.get("title") or ""),
                episode_number=e.get("episode_number"),
                series=_clean(e.get("series")) or _clean(data.get("playlist_title")),
                author=_clean(e.get("artist") or e.get("creator") or e.get("artist_name")),
                uploader=_clean(e.get("uploader")),
                extractor=e.get("extractor_key"),
                original=e,
            )
            if ei.url:
                items.append(ei)
        return ProbeResult(kind=mrz_kind or "playlist",
                           url=url, title=title, series=series,
                           uploader=uploader, extractor=extractor, entries=items)

    # Single item
    return ProbeResult(kind=mrz_kind or "episode",
                       url=url, title=title, series=series,
                       uploader=uploader, extractor=extractor, entries=[
        EpisodeItem(
            url=_prefer_page_url(data, base_url=url),
            title=_clean(data.get("title") or ""),
            episode_number=data.get("episode_number"),
            series=_clean(data.get("series")) or _clean(data.get("playlist_title")),
            author=_clean(data.get("artist") or data.get("creator") or data.get("artist_name")),
            uploader=_clean(data.get("uploader")),
            extractor=data.get("extractor_key"),
            original=data,
        )
    ])