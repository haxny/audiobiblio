# Autoloop charter — self-improving implementation loop

Started 2026-11-20 on the user's instruction: *"naplanuj, priprav a spust
sebezlepsovaci loop implementace a vylepsovani stavajicich funkcionalit; kdyz
budes potrebovat misto, maz cache a nepotrebne soubory; kdyz nezareaguju na
request, nech rozhodnout agenta; az se ozvu, rekni, co se povedlo a co ne."*

The loop works through `BACKLOG.md` top to bottom, one item per iteration, and
writes every iteration to `LOG.md`. The user reads `LOG.md` when they return.

## One iteration

1. **Stop check.** If `docs/autoloop/STOP` exists → stop, write a final summary.
2. **Pick** the first open item in `BACKLOG.md` (skip items marked BLOCKED).
3. **Plan** in 5–10 lines (LOG.md): goal, files, risk, how it is verified.
4. **Test first** (RED), implement (GREEN), refactor. Python 3.12 venv in `.venv`.
5. **Verify**: full `pytest`, `lint-imports` (layer contract). Both must pass.
6. **Data-touching change** → run it first on a *copy* of the live DB
   (scratchpad), report counts. If it would create downloads, moves, merges or
   deletes beyond the budgets below → do NOT deploy; branch + log + ask.
7. **Ship** only if safe (see Deploy rules): commit, push, deploy, health check.
   Otherwise commit on branch `autoloop/<item>` and mark the item NEEDS-DECISION.
8. **Log** result honestly: what worked, what failed, what was not verified.
9. Three failed iterations in a row → pause the loop and write the report.

## Hard limits (never, even if "the agent decides")

- Never delete, move, rename or overwrite user media or archives: `eBOOKs`,
  `audiobooks`, `fiction`, `nonfiction`, `music`, `video*`, `backup*`, `arg*`,
  `brno*`, `caseflow*`, `nfs`, `nfs2`, `TM-MK`, Synology Drive data, photos.
- Never run bulk merges / finalize / follow-paths `--apply` on the live DB
  without the user (dry-run reports only). Single-book merges the user already
  approved are done and listed in LOG.md.
- Never change DSM settings, stop other services, touch other projects/repos,
  rewrite git history or force-push. Never send private data to external
  services (rAPI reads of public data are fine).
- Legal/case files (`arg*`, `caseflow*`, `brno*`, Datovka) are out of scope.

## Disk space

Free space is scarce (volume3 ~100 %). When space is needed the loop MAY delete,
logging path + size in LOG.md for each deletion:

- caches: pip / docker build cache, dangling docker images, `__pycache__`,
  `.pytest_cache`, yt-dlp / ffmpeg temp, files the loop itself created in
  `/tmp` or the scratchpad (including DB copies);
- audiobiblio's own expired trash (per its retention), app logs older than 30 days.

Nothing else. Anything outside this list is a *request*: ask the user; if the
user does not answer within one wake-up cycle, the **decision goes to an
independent reviewer agent** (Agent tool) that gets this charter and the facts.
Its decision and reasoning are logged. The hard limits above still win: an agent
cannot authorise deleting user media — such items are deferred.

## Deploy rules

- Not between 02:00 and 06:00 Europe/Prague (nightly jobs).
- DB migration → backup `db.sqlite3` first (`sqlite3 .backup`), keep it.
- After deploy: health 200, job list registered, no errors in the first minutes
  of logs. Failure → redeploy previous commit, log it as FAILED.
- Download budget: a change must not queue more than 30 new downloads per night
  nor when free space on the target volume is below 100 GB.

## Reporting

`LOG.md` is the single source of truth: date, item, outcome (DONE / PARTIAL /
FAILED / NEEDS-DECISION), evidence (test counts, dry-run numbers), deployed
commit. No claim of "fixed" without an independent verification step (see
backlog item V1).
