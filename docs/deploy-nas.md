# NAS Deployment Guide

Target: Synology NAS running DSM 7 with Container Manager (Docker).

---

## 1. Prerequisites

- **Container Manager** installed from the Synology Package Center (DSM 7.2+).
- SSH access to the NAS: `ssh <user>@<NAS_IP>`.
- At least 4 GB free on the volume that will hold the Docker data and media.
- Verify the NAS CPU architecture: `uname -m` — must be `x86_64` (most modern Synology units). ARM-based units need a cross-compiled image; see Option B below.

---

## 2. Build Options

### Option A: Build on the NAS from a git clone (simplest)

```bash
ssh <user>@<NAS_IP>
# Install git if missing (e.g. via SynoCommunity package or ipkg)
git clone https://github.com/<your-fork>/audiobiblio.git /volume1/docker/audiobiblio
cd /volume1/docker/audiobiblio
docker build -t audiobiblio:latest .
```

### Option B: Cross-compile on a laptop, transfer to NAS

Use this when building on the NAS is slow or unavailable.

```bash
# On your laptop (Docker Desktop required, with multi-platform support)
docker buildx build \
  --platform linux/amd64 \
  -t audiobiblio:latest \
  --output type=docker \
  -f Dockerfile \
  . | gzip > audiobiblio-latest.tar.gz

# Transfer to NAS
scp audiobiblio-latest.tar.gz <user>@<NAS_IP>:/volume1/docker/

# Load on NAS
ssh <user>@<NAS_IP>
docker load < /volume1/docker/audiobiblio-latest.tar.gz
```

> Note: substitute `linux/arm64` instead of `linux/amd64` if `uname -m` on the NAS returns `aarch64`.

---

## 3. Compose Up

Place `docker-compose.yml` and `config.yaml` on the NAS:

```bash
ssh <user>@<NAS_IP>
mkdir -p /volume1/docker/audiobiblio
# copy files from laptop:
scp docker-compose.yml config.yaml <user>@<NAS_IP>:/volume1/docker/audiobiblio/
cd /volume1/docker/audiobiblio
```

Set the media path (adjust to your Synology volume and share name):

```bash
export MEDIA_PATH=/volume3/eBOOKs/audiobooks
export ABS_URL=http://<NAS_IP>:13378
export ABS_API_KEY=<your-abs-api-key>
docker compose up -d
```

The `media` volume binds to `$MEDIA_PATH`; the `data` volume is managed by Docker and holds the SQLite database.

---

## 4. DB Carry-Over (CRITICAL before first start)

All crawl curation, approvals, download history, and metadata provenance live in the local `db.sqlite3`. Transfer it before the container starts for the first time.

**Stop the container first (if already started):**

```bash
docker compose stop audiobiblio
```

**Locate the Docker data volume on the NAS:**

```bash
docker volume inspect audiobiblio_data
# Look for "Mountpoint" — typically /volume1/@docker/volumes/audiobiblio_data/_data
```

**Back up any existing DB on the NAS (safety first):**

```bash
cp /volume1/@docker/volumes/audiobiblio_data/_data/db.sqlite3 \
   /volume1/@docker/volumes/audiobiblio_data/_data/db.sqlite3.bak 2>/dev/null || true
```

**Copy from your laptop:**

```bash
# On your laptop — copy all WAL files if present
for f in db.sqlite3 db.sqlite3-wal db.sqlite3-shm; do
  [ -f "$f" ] && scp "$f" <user>@<NAS_IP>:/volume1/@docker/volumes/audiobiblio_data/_data/
done
```

> WARNING: Never overwrite a running DB. Always `docker compose stop audiobiblio` first. If `-wal` / `-shm` files exist on the laptop, copy them too — SQLite may be mid-transaction without them.

**Restart after copy:**

```bash
docker compose up -d audiobiblio
```

---

## 5. First-Start Checklist

1. **Alembic auto-upgrade** — the entrypoint runs `alembic upgrade head` before `audiobiblio serve`. Watch logs:
   ```bash
   docker compose logs -f audiobiblio | head -30
   ```
   Expect `INFO  [alembic.runtime.migration] Running upgrade ...` lines followed by `web_started`.

2. **Health check** — wait ~10 s then:
   ```bash
   curl -s http://<NAS_IP>:8080/api/v1/health
   # Expected: {"status":"ok","scheduler_running":true}
   ```

3. **Verify crawl targets** — open `http://<NAS_IP>:8080/targets` and confirm your registered targets appear with correct crawl states.

4. **Timezone** — add `TZ=Europe/Prague` (or your zone) to the environment block in `docker-compose.yml` if scheduled times drift.

---

## 6. Laptop → NAS Inbox Flow

For audiobooks downloaded or ripped on a laptop and handed off to the NAS library:

1. Create a dedicated Synology shared folder, e.g. `/volume3/eBOOKs/_inbox`.
2. Add a Synology Drive sync task (or an rsync cron on the laptop) pointing at that folder.
3. In `docker-compose.yml`, uncomment the inbox volume and env lines:
   ```yaml
   environment:
     - AUDIOBIBLIO_INBOX_DIRS=/media/_inbox
   volumes:
     - inbox:/media/_inbox
   ```
   Add the named volume at the bottom:
   ```yaml
   volumes:
     inbox:
       driver: local
       driver_opts:
         type: none
         o: bind
         device: /volume3/eBOOKs/_inbox
   ```
4. Open `http://<NAS_IP>:8080/import`, click **Scan**, and review findings under the UNKNOWN / MATCHED tabs.
5. Accept findings once verified — `move=True` relocates files to the managed library path.

---

## 7. eBOOKs First Scan Advice

The `AUDIOBIBLIO_LIBRARY_DIR` points at an existing audiobook collection that was not managed by audiobiblio.

- **Scanning is read-only** — it creates `import_findings` rows but does NOT move or rename any file.
- **Do not accept findings in bulk.** Review every MATCHED finding before accepting: confirm the episode, author, and file are what you expect.
- Start with a small subdirectory by temporarily setting a narrower `library_dir`, scan, review, then widen.
- Any accept with `move=True` will relocate files to the canonical path — only do this after confirming the naming convention looks right on 5–10 samples.

---

## 8. Updating

```bash
ssh <user>@<NAS_IP>
cd /volume1/docker/audiobiblio
git pull                          # or re-transfer a new audiobiblio-latest.tar.gz + docker load
docker compose build              # rebuild with the new code (skip if using docker load)
docker compose up -d              # rolling restart; data volume is preserved
docker compose logs -f audiobiblio | head -20
```

The `data` volume (SQLite DB + downloaded files) survives the rebuild. Alembic runs on startup and applies any new migrations automatically.
