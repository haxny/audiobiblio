"""Dual-source pairing: station target -> mujrozhlas counterpart."""
from unittest.mock import patch

from audiobiblio.sources.pairing import derive_mujrozhlas_counterpart, ensure_pair


def test_station_url_derives_via_show_redirect():
    with patch("audiobiblio.sources.pairing._resolve") as res:
        res.side_effect = lambda u, timeout=30: (
            "https://www.mujrozhlas.cz/poctenicko"
            if "show-redirect/6370902" in u else None
        )
        out = derive_mujrozhlas_counterpart(
            "https://olomouc.rozhlas.cz/poctenicko-6370902")
    assert out == "https://www.mujrozhlas.cz/poctenicko"


def test_mujrozhlas_url_is_not_derived():
    assert derive_mujrozhlas_counterpart(
        "https://www.mujrozhlas.cz/poctenicko") is None


def test_ensure_pair_sets_and_persists(db_session):
    from audiobiblio.core.db.models import CrawlTarget, CrawlTargetKind
    t = CrawlTarget(url="https://olomouc.rozhlas.cz/poctenicko-6370902",
                    kind=CrawlTargetKind.PROGRAM)
    db_session.add(t); db_session.commit()
    with patch("audiobiblio.sources.pairing.derive_mujrozhlas_counterpart",
               return_value="https://www.mujrozhlas.cz/poctenicko"):
        assert ensure_pair(db_session, t) is True
    assert t.paired_url == "https://www.mujrozhlas.cz/poctenicko"
    # second call is a no-op
    assert ensure_pair(db_session, t) is False
