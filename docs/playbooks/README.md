# Playbooks: the self-improving loop

Every fix the user points at is a **case**. A case is never a one-off: it is
turned into a playbook (detector + procedure), run on the whole database,
and the verdicts on its proposals tune the playbook. GUI: http://nasx:8321

```
   user points at a problem            nightly / weekly detectors
              \                                  /
               v                                v
   1 OBSERVE ── case logged (cases.jsonl: what, where, root cause)
        |
   2 DIAGNOSE ── root cause in code or data? (code → test + fix, deploy)
        |
   3 FIX INSTANCE ── the case itself, journaled, undoable
        |
   4 GENERALIZE ── playbook: detector (SQL/probe) + procedure + confidence rules
        |
   5 SCAN ── detector over the whole DB, read-only → candidate list
        |
   6 PROPOSE ── confident → auto-apply (journaled); uncertain → review queue
        |                                     (user sees only uncertainty)
   7 VERDICT ── user accepts / rejects / corrects
        |
   8 LEARN ── verdicts tighten confidence rules; recurring code causes get a
        |      regression test; detector thresholds updated in the playbook
        v
   9 MONITOR ── detector count per run on /system; a rising count = new
                regression → back to 1
```

Rules
- Detectors are read-only. Applying is a separate, journaled step.
- Confident = two independent sources agree (e.g. station page + panacek
  catalog year/length, file tag comment = page summary). One source = review.
- Files on disk: never rename directories, never delete; duplicates go to the
  duplicate review (http://nasx:8321/dup-review).
- External sources (webshare, mluvenypanacek) only *propose* candidates;
  nothing is downloaded without the user's approval.
- Each playbook keeps a "Cases" section: date, instance, outcome, what was
  learned. That section is the training data for the next version.

Playbooks
- [program-recovery.md](program-recovery.md) — finished/expired program:
  wrong station, empty crawl, archive metadata, owned copies, external search.
