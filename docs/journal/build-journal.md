# Phase 1 Foundation — progress ledger
Task 1: complete (commits dc7334d..4745460, review clean)
  Minor (for final review triage): test_marker_triggers_recode only asserts fixed != corrupted, could assert "hřbitov"; no [tool.pytest.ini_options] testpaths; report ran pytest -v not -q.
Task 2: complete (commits 4745460..987728f, review clean)
  Minor: len<=250 bound loose; forbidden-char asymmetry uncommented.
Task 3: complete (commits 987728f..eb02055, review clean, no findings)
Task 4: complete (commits eb02055..53bf0b3, review clean)
  Minor: crawler.py uses relative import vs dedupe.py absolute (normalize in Task 5); crawler _norm_url slash semantics changed single->all trailing (intentional consolidation).
Task 5: complete (commits 53bf0b3..12c3513: 31d0034 restructure + 12c3513 abs scripts, re-review clean)
  Notes: unidecode added to deps (pre-existing gap); brief said 14 CLI commands, actual 16; library/pipelines is namespace pkg (no __init__.py) — may matter for import-linter/packaging.
Task 6: complete (commits 12c3513..96a55e7: a79575f contract + 470b5f9 dead-import fix + 96a55e7 testpaths, re-review clean)
  4 violations parked (3x acquire->library runtime pipeline calls, 1x cli->web bootstrap) with TODO(phase2) + docs/decisions note. Minor: task-6-report.md main body stale (says 5 parked).
Task 7: complete (commits 96a55e7..71dd7ea, review clean)
  Minor for later: SAEnum stores names ("SCRAPED") not values ("scraped") — Phase 4 footgun for raw SQL; single-col indexes partially redundant; datetime.utcnow deprecated (project-wide pattern).
Task 8: complete (commits 71dd7ea..c21d181: a5e95cd mining + c21d181 accuracy fixes, re-review clean)
  7 dead-ends + 2 decisions; archive/ deleted. SFT rename workflow (RadioSeriesOrganizer / Episode.format_filename) flagged as possibly-lost one-off — in git history + report.
  Minor: 0002 says "13 versions" vs "14 files" (cosmetic); 0007 is 26 lines vs 25 ceiling.
Task 9: complete (commits c21d181..b62d3e4: 054cf31 docs + b62d3e4 tag-fixer entry fix, review clean; 3 warn-items resolved by controller)
  Minor: Layer-2 (cli) numbering gap unexplained in docs; acquire.md/library.md dual tag_audio path could be clearer.
Task 10: complete (gate PASS after fix 7ec2351 — 5 stale .db imports in cli.py caught by target-list smoke; suite 52, lint-imports kept, CLI+web+packaging green, alembic head 059c3c38a79a)
Final review: With fixes -> fix wave 014ded3 (6 stale imports + docker-compose + guard test + 2 more intra-sources relatives). Gate re-run: 53 passed, lint-imports KEPT, CLI/serve/scheduler/compose verified. BRANCH READY TO MERGE.

## Phase 2: Daily Loop (plan 2026-07-02-phase2-daily-loop.md, branch feature/phase2-daily-loop off 490b331)
P2 Task 1: complete (490b331..1e1132e, review clean). episode_factory creates NO assets (plan_downloads auto-creates via ensure_assets_for_episode) — Task 4 dispatch must use AssetType.AUDIO directly, not ep.assets[0].type.
P2 Task 2: complete (1e1132e..de77749, review clean). ApprovalMode + migration + API. Dev deps: httpx2, StaticPool web conftest. Minor: no 400-branch test (ride).
P2 Task 3: complete (de77749..00d1a71, review clean). approval_mode threaded via crawl_target->helpers; manual ingest stays legacy threshold. Minor: mid-file test import (ride).
P2 Task 4: complete (00d1a71..17eb2e3, review clean). Reject endpoints. Minor: reject-all DB-state assertion, utcnow idiom (inherited, ride).
P2 Task 5: complete (17eb2e3..997e85a, review clean). Infosoud UI shell live, Pico removed, ALL 12 TemplateResponse calls migrated (Starlette API change had every HTML page 500ing — pre-existing, now fixed). routers/ have no TemplateResponse (checked). Minor: --pico-* shim vars need explanatory comment (ride).
P2 Task 6: complete (997e85a..c40c9f3, review clean). Console live. Lost actions to reinstate in Task 9: "Run Jobs" (/api/v1/jobs/run) button; "ABS Scan" (/api/v1/system/abs-scan) — park ABS scan for System page (phase 6) unless trivial.
P2 Task 7: complete (c40c9f3..e3deabf, review clean). Inbox live. Minor: target_hint key not emitted (unused by template, YAGNI-ride); no "?"-fallback test (ride).
P2 Task 8: complete (e3deabf..3a4f817: 12e4349 sources page + 3a4f817 crawl-now persistence fix, both reviewed clean).
  E2E: Cetba na pokracovani crawled live — 71 discovered, 27 new APPROVAL, inbox renders 96. Minor rides: reload-on-error for hx actions, onclick vs onchange, hx-confirm escaping.
P2 Task 9: complete (3a4f817..7868303, review clean). Downloads page live; SSE named events verified WORKING (brief's assumption corrected by implementer). Minor: skipped-tab active visual, SSE 30s reconnect gap (poll covers).
P2 Task 10: GATE PASS (finished by controller after gate agent hit session limit mid-run):
  suite 72 / lint KEPT / alembic head 20f737dc3b98 / all 10 routes 200, pico=0, badge=122.
  REVIEW e2e: job 18390 approved -> downloaded "Jaroslav Hasek: Z Dejin Strany mirneho pokroku" 29min/86MB, artist+album tags OK (diacritics stripped).
  AUTO e2e: mode=auto -> 20 new jobs straight to PENDING -> scheduler downloaded 2 real episodes unattended; re-air jobs resolved against existing files. Target restored to REVIEW.
  crawl-now last_crawled_at fix verified live (timestamp persisted).
  Phase 3 findings: title (©nam) + genre tags EMPTY on downloaded file; trkn total wrong (16/3); META_JSON/WEBPAGE assets of older ep stayed MISSING.
Final P2 review: Ready to merge (Yes). Polish commit 5245d02 (7 items). BRANCH READY.

## Phase 3: Quality & Upgrades (plan 2026-07-03-phase3-quality-upgrades.md, branch feature/phase3-quality-upgrades off 50ce019)
P3 Task 1: complete (50ce019..9a1f495, review clean). Cascade + per-episode inbox. Minors ride: response_model dropped (restore in polish), AUDIO-path preference literal deviation (no effect), inline import.
P3 Task 2: complete (9a1f495..97314a2: 3572aba tag fixes + 97314a2 review fixes, re-review clean). Plain track numbers (N,0), title-when-differs rule, genre atom pinned, ffmpeg fixture. Minor ride: stale test docstring mentions removed mock.
P3 Task 3: complete (97314a2..a5ea5c7: d51a8fa mediainfo + a5ea5c7 isolation fixes, re-review clean). Asset quality fields live; 20 real assets backfilled; bitrate stored in bps.
P3 Task 4: complete (a5ea5c7..72e25e6: c437e1c trash + 72e25e6 boundary tests, re-review clean). Trash module live: dated folders, sidecars, strict-< purge, daily scheduler job.
P3 Task 5: complete (72e25e6..0dbf666: ad1572c upgrades + 0dbf666 isolation+edge tests, re-review clean). upgrade_candidates table (rev 8e3696d70603), evaluate_reair 5-branch decision, abs() ad rule, wired into url_reair ingest path.
P3 Task 6: complete (0dbf666..e2386ad: e83a7c8 carryover + e2386ad n/a guard, re-review clean). carry_over_tags: 14 fields, old-wins, n/a treated empty, old file byte-untouched.
P3 Task 7: complete (e2386ad..3046eb4: da11b8a resolve flow [Approved] + 3046eb4 test hardening). Stage/resolve API live, crash-safe replace, no deletes anywhere. Minor rides: apply_media_info success-path fields untested on real audio (gate covers), utcnow idiom.
P3 Task 8: complete (3046eb4..4afa271, review clean). Upgrades UI in inbox, apiJson extracted to static JS. Minors ride: floor-div sign edge (<5s diffs), defer on script tag, unused staged_path key.
P3 Task 8b (user-requested, unplanned): UI DENSITY PASS — binding preference (see memory feedback_ui_density).
P3 Task 8b: complete (4afa271..82c2e23, review clean). Density pass. Low ride: .dense-table ellipsis inert without table-layout:fixed — revisit if long titles overflow in practice.
FINDINGS from user testing (2026-07-04):
- Generic title "Epizody poradu" leaked into episode title AND filename (SFT ep 9) — generic placeholders must never become titles/filenames. Phase 5 candidate or gate-adjacent fix.
- UI gap: no click-through from SUCCESS job to file/episode detail; Library page lacks file paths + preview player. Phase 5 candidate (spec §5 manage+preview).
- User's live server runs main (P2) — P3 tag fixes apply only after merge; old files re-taggable via tags CLI.
P3 Task 9: complete (82c2e23..e5844b0: 2036988 dedupe page + e5844b0 safety fixes, re-review clean). Clusters+merge live; self-merge guarded, buttons fixed (fetch/apiJson), aliases re-pointed. Cosmetic ride: dry-run action log says "re-point" where real path dedup-deletes redundant self-alias; ValueError→404 unreachable path.
P3 Task 10: GATE PASS (agent died mid-report; controller re-verified: suite 169, lint KEPT, head 8e3696d70603, queue drained, synthetic candidate cleaned, docs 5defb93).
  PHASE 4 FINDING: 301/359 COMPLETE audio assets have DEAD file paths (March-era layout since reorganized) — DB<->disk reconciliation is the first Phase 4 job.
Final P3 review: With fixes -> fix wave 7bc5eeb (child-row cleanup + flush-before-trash + absolute staging + REPLACED-before-mediainfo). Suite 173, lint KEPT. BRANCH READY TO MERGE.

## Phase 4: Sync & Import (plan 2026-07-06-phase4-sync-import.md, branch feature/phase4-sync-import off eabeb29)
P4 Task 1: complete (eabeb29..98ceb44: d758164 verify-files + 98ceb44 minors, review clean). Real dry-run: 484 checked / 149 ok / 335 MISSING (dead paths incl. metadata files).
P4 Task 2: complete (98ceb44..502bccd, review clean; alias-path warn resolved by controller grep — only guarded sites write titles). Generic-title guard live (ingest both paths + stem + tag defense). Minors ride: ep-branch test gap, 2 set entries unevidenced.
P4 Task 3: complete (502bccd..4124018, review clean). SCRAPED provenance live on both ingest paths. Minors ride: >= vs > timestamp assert, truthy vs non-None guards, entity_id assert gap.
P4 Task 4: complete (4124018..c3b889c: 4168b69 manual edits + c3b889c churn fix, re-review clean). PATCH metadata + MANUAL protection; crawls can never clobber user edits; author enrichment set-only-when-empty.
P4 Task 5: complete (c3b889c..f8131b3: f69046e sync engine + 3 fix rounds, approved). DB->file projection live; M4A-unreadable guard (exiftool absence can't destroy tags — NAS-safe); write failures reported. Deployment note: Dockerfile lacks exiftool — add in Phase 6 image or rely on guard.
P4 Task 6: complete (f8131b3..3f9971c: 95f55bb importer + 3f9971c fixes, re-review clean). Scanner+import_findings live: dead-path recovery, program-scoped title matching, duplicate replace-via-trash, provenance on accept. Minor ride: DUPLICATE-path provenance assertion.
P4 Task 7: complete (3f9971c..ef10d18: 3d7e5da import page + b96df2a XSS fix + 21e61b6 unused import + ef10d18 inbox-empty 400 [spec correction from peer], re-review clean).
  Import page live: /api/v1/import (scan/findings/accept/ignore), /import view, nav after Dedupe, console badge when >0. Suite 266 (13 new), lint-imports KEPT.
  Minors ride (for final review triage): db.add() no-op on tracked finding (importer.py router accept); uuid imported inside _do_import_scan body; count() carries joinedload option; row actions use custom doAccept/doIgnore fetch instead of apiJson (justified — targeted row removal); 4 pre-existing ruff issues in views.py (project lint gate is import-linter, ruff not a dep).
P4 Task 8: complete (ef10d18..6b53bc4, review clean). Episode detail + player (Range 206 works — plan assumption corrected). Minors ride: JSDoc orphan, hidden audio element, narrator/genre Current always dash (by design), work_id None comment.
P4 Task 9: GATE PASS (25e6d4a docs + 63e5e2e accept-FAILED fix). Real data: 335 dead paths fixed->MISSING; scan 752 findings (4 matched-title accepted+repaired, 748 unknown for user review); sync write proven on ep 25 (title "(test)" — user to revert in UI); 13 generic titles cleaned; audio 200+206. BRANCH AT 287 TESTS.
Final P4 review: With fixes -> fix wave 7cad603 (unified WORK_FIELDS routing in core.provenance, TAG_TO_DB canonical recording, generic-title guard on accept, 400/409 endpoint guards). Suite 294, lint KEPT. BRANCH READY TO MERGE.

## Phase 5: Enrichment, Gaps & NAS (plan 2026-07-07-phase5-enrichment-gaps.md, branch off main)
P5 Task 1: complete (d6f50df..ff57ac7: c8bba52 enrich_meta + ff57ac7 doc/test fixes, review clean). REAL DATA: 59 checked, 25 updated (11 titles incl. "Episode 9"->Karel Horky, 14 descriptions); self-caught MD5+prefix guards approved. Hook live for future downloads.
P5 Task 2: complete (ff57ac7..37afe6a: 28adb25 deploy prep + 37afe6a XDG fix, re-review clean). CRITICAL caught: guide targeted /app/data but container read /root/.local/share — DB would silently vanish AND never persisted; fixed via XDG_DATA_HOME/XDG_STATE_HOME=/app/data (logs persist too; config/cache ephemeral-but-unused, latent note). exiftool in image, healthcheck /api/v1/health, deploy-nas.md ready. Docker daemon absent locally — first real build happens on NAS.
P5 Task 3: complete (37afe6a..0ef79d7, review clean). target_state helper + Console overdue badges + crawl-status CLI. Minors ride: double-computation (4 targets, moot), LIMIT-20 counter scope, test naming.
P5 Task 4: complete (0ef79d7..aafbc99: 66868ea completeness + aafbc99 dedupe/batch fixes, re-review clean). expected_total + /gaps + gap-fill priority live. Cosmetic ride: complete_audio_count docstring overclaims one caller.
P5 Task 5: complete (aafbc99..541b9d7: 446ca0c paste-URL [stalled agent's work finished by follow-up] + 541b9d7 fetch fix, re-review clean). Paste-episode-URL -> whole-program offer live; Critical caught: apiJson reload killed the crawl+redirect flow.
P5 Task 6: complete (541b9d7..6e7fa40: 91bc874 dbk enrichment + 6e7fa40 never-raise fix, review approved w/ fixes applied — mechanical fixes verified by test evidence, skipping formal re-review round). databazeknih live: search+fetch fixtures, ENRICHED provenance, re-enrich button.
P5 Task 7: complete (6e7fa40..b85677e, wrong-base incident self-recovered + rebuilt, review Approved). Finalize live (explicit, previewed, flush-per-op). GATE TODO: docs wording "crash safety"->"session consistency"; str(sidecar) not .resolve()d (one-liner); JS dup in two templates (ride).
P5 Task 8: GATE PASS (agent stalled mid-run; controller completed). Fixes: 0de317a sync generic-title guard (REAL bug: generic FILE obs outranked enriched title), 7585851 series-kind parent offer. Enrich circle proven on disk. STRUCTURAL FINDING: works are program-level, not per-book — next-phase priority (blocks dbk matching, real completeness, finalize semantics).
Final P5 review: With fixes -> fix wave 8812130 (finalize planned-set parity + exact-filename tests, DOM-wired paste buttons, series UI gate, provenance-only commit, ADR 0003 works-are-program-level). Suite 465+2, lint KEPT. BRANCH READY TO MERGE.
