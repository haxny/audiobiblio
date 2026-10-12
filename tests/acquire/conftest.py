import pytest


@pytest.fixture(autouse=True)
def _no_rapi_serials(monkeypatch, request):
    """Crawler tests never reach api.mujrozhlas.cz for serial identity
    (tests that need it patch crawler._rapi_serials themselves)."""
    import audiobiblio.acquire.crawler as crawler_mod
    monkeypatch.setattr(crawler_mod, "_rapi_serials", lambda target: {})
