"""Generic serial revival: env SERIAL (uuid), WORK_ID, TOTAL (opt), GO=1."""
import os
import urllib.request, json as _json

from audiobiblio.core.db.models import (
    Episode, DownloadJob, Work, JobStatus, AssetType, AvailabilityStatus,
)
from audiobiblio.core.db.session import get_session

GO = os.environ.get("GO") == "1"
SERIAL = os.environ["SERIAL"]
WORK_ID = int(os.environ["WORK_ID"])
TOTAL = int(os.environ.get("TOTAL") or 0)

req = urllib.request.Request(
    f"https://api.mujrozhlas.cz/serials/{SERIAL}/episodes",
    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
data = _json.loads(urllib.request.urlopen(req, timeout=30).read())
s = get_session()
work = s.get(Work, WORK_ID)
assert work is not None, f"work {WORK_ID} neexistuje"
created = 0
for e in data["data"]:
    a = e["attributes"]
    uuid = e["id"]
    if s.query(Episode).filter_by(ext_id=uuid).first():
        print(f"  part {a.get('part')}: uz existuje, skip")
        continue
    links = a.get("audioLinks") or []
    hls = next((l for l in links if l.get("variant") == "hls"), None) or (links[0] if links else None)
    if not hls:
        print(f"  part {a.get('part')}: BEZ AUDIA, skip")
        continue
    ep = Episode(
        work_id=WORK_ID, ext_id=uuid,
        title=a.get("title") or a.get("shortTitle") or f"{a.get('part')}. díl",
        episode_number=a.get("part"), url=hls["url"],
        duration_ms=(hls.get("duration") or 0) * 1000 or None,
        summary=a.get("description"),
        availability_status=AvailabilityStatus.AVAILABLE,
        auto_download=True, priority=100,
        discovery_source="manual-serial-revival",
    )
    if GO:
        s.add(ep)
        s.flush()
        s.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                          status=JobStatus.PENDING,
                          reason=f"expiring serial revival: {SERIAL[:8]}"))
    created += 1
    print(f"  part {a.get('part')}: {ep.title!r}", "OK" if GO else "(dry)")
if GO:
    if TOTAL:
        work.expected_total = TOTAL
    s.commit()
print(f"{'CREATED' if GO else 'WOULD CREATE'}: {created}")
