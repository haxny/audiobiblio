import pytest


@pytest.fixture(autouse=True)
def _no_rapi_serials(monkeypatch, request):
    """Crawler tests never reach api.mujrozhlas.cz for serial identity
    (tests that need it patch crawler._rapi_serials themselves)."""
    import audiobiblio.acquire.crawler as crawler_mod
    monkeypatch.setattr(crawler_mod, "_rapi_serials", lambda target: {})
    # ...nor for show resolution: AUTO targets now try rAPI first, and an
    # unresolvable show falls back to the (stubbed) full crawl
    import audiobiblio.acquire.rapi_crawl as rapi_mod
    monkeypatch.setattr(rapi_mod, "resolve_show_uuid", lambda *a, **k: None)
