"""The live recorder is wired into the app scheduler and honours the switch."""
from __future__ import annotations

from audiobiblio.acquire import scheduler as sched


def test_live_jobs_registered():
    s = sched.create_scheduler()
    jobs = {j.id: j for j in s.get_jobs()}
    assert "live_lookahead" in jobs and "live_tick" in jobs
    assert jobs["live_tick"].trigger.interval.total_seconds() == sched.LIVE_TICK_SECONDS


def test_live_jobs_noop_when_disabled(monkeypatch):
    from audiobiblio.core.config import Config
    monkeypatch.setattr(sched, "load_config", lambda: Config(live_recorder_enabled=False))
    called = []
    import audiobiblio.acquire.live.recorder as rec_mod
    monkeypatch.setattr(rec_mod, "default_recorder", lambda: called.append(1))
    sched._live_tick_job()
    sched._live_lookahead_job()
    assert called == []


def test_live_tick_job_runs_default_recorder(monkeypatch, db_session):
    from audiobiblio.core.config import Config
    import audiobiblio.acquire.live.recorder as rec_mod
    import audiobiblio.core.db.session as sess_mod
    monkeypatch.setattr(sched, "load_config", lambda: Config())
    monkeypatch.setattr(sess_mod, "get_session", lambda: db_session)
    ticks = []

    class R:
        def tick(self, s):
            ticks.append(s)
            return {"started": 0}
    monkeypatch.setattr(rec_mod, "default_recorder", lambda: R())
    sched._live_tick_job()
    assert ticks == [db_session]


def test_config_env_overrides(monkeypatch, tmp_path):
    from audiobiblio.core.config import load_config
    monkeypatch.setenv("AUDIOBIBLIO_LIVE_STATIONS", "d-dur, plus")
    monkeypatch.setenv("AUDIOBIBLIO_LIVE_RECORDER", "off")
    cfg = load_config(tmp_path / "none.yaml")
    assert cfg.live_stations == ["d-dur", "plus"]
    assert cfg.live_recorder_enabled is False
