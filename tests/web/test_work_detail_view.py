"""/works/{id} — the one page per book: parts in reading order, audio
status, inline player, completeness (user: 'kde to najdu?')."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Base, Episode, Program, Series, Station,
    Work,
)
from audiobiblio.web.deps import get_db


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def book(db_session):
    st = Station(code="CRo2", name="Dvojka")
    db_session.add(st)
    db_session.flush()
    prog = Program(station_id=st.id, name="Četba na pokračování")
    db_session.add(prog)
    db_session.flush()
    ser = Series(program_id=prog.id, name="Četba na pokračování")
    db_session.add(ser)
    db_session.flush()
    work = Work(series_id=ser.id, title="Testovací kniha", author="Jan Autor")
    db_session.add(work)
    db_session.flush()
    # Parts inserted OUT of order — the page must sort by episode_number.
    for n, status in [(2, AssetStatus.COMPLETE), (1, AssetStatus.COMPLETE), (3, None)]:
        ep = Episode(work_id=work.id, title=f"Testovací kniha díl {n}", episode_number=n)
        db_session.add(ep)
        db_session.flush()
        if status:
            db_session.add(Asset(
                episode_id=ep.id, type=AssetType.AUDIO,
                status=status, file_path=f"/media/x/{n}.m4a"))
    db_session.flush()
    return work


@pytest.fixture()
def client(db_session):
    from audiobiblio.web.views import router as views_router

    app = FastAPI()
    app.include_router(views_router)

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=True)


class TestWorkDetail:
    def test_returns_200(self, client, book):
        assert client.get(f"/works/{book.id}").status_code == 200

    def test_404_unknown(self, client, book):
        assert client.get("/works/99999").status_code == 404

    def test_shows_title_author_and_completeness(self, client, book):
        t = client.get(f"/works/{book.id}").text
        assert "Testovací kniha" in t
        assert "Jan Autor" in t
        assert "2/3 dílů staženo" in t

    def test_parts_in_reading_order(self, client, book):
        t = client.get(f"/works/{book.id}").text
        assert t.index("díl 1") < t.index("díl 2") < t.index("díl 3")

    def test_player_and_program_breadcrumb(self, client, book):
        t = client.get(f"/works/{book.id}").text
        assert "work-player" in t
        assert "Četba na pokračování (CRo2)" in t

    def test_route_registered(self):
        from audiobiblio.web.views import router as views_router
        paths = [getattr(r, "path", None) for r in views_router.routes]
        assert "/works/{work_id}" in paths


class TestPendingPairBadge:
    def test_two_versions_badge_when_upgrade_pending(self, client, book, db_session):
        from audiobiblio.core.db.models import Episode, UpgradeCandidate, UpgradeStatus
        ep = db_session.query(Episode).filter_by(work_id=book.id, episode_number=1).one()
        db_session.add(UpgradeCandidate(
            episode_id=ep.id,
            candidate_url="file:///media/fiction/x.m4a",
            candidate_duration_ms=100_000, owned_duration_ms=95_000,
            status=UpgradeStatus.PENDING_REVIEW,
            note="test pair",
        ))
        db_session.flush()
        t = client.get(f"/works/{book.id}").text
        assert "2 verze" in t
        assert "test pair" in t

    def test_no_badge_without_pending(self, client, book):
        assert "2 verze" not in client.get(f"/works/{book.id}").text


class TestEpisodePairComparison:
    """Episode detail renders the second-version card with its own player
    (user: 'nevidím dva soubory, abych je mohl srovnat')."""

    def test_pair_card_and_candidate_player(self, client, book, db_session, tmp_path):
        from audiobiblio.core.db.models import Episode, UpgradeCandidate, UpgradeStatus
        ep = db_session.query(Episode).filter_by(work_id=book.id, episode_number=1).one()
        staged = tmp_path / "candidate.m4a"
        staged.write_bytes(b"\x00" * 64)
        uc = UpgradeCandidate(
            episode_id=ep.id,
            candidate_url=f"file://{staged}",
            candidate_duration_ms=1_448_100, owned_duration_ms=1_417_600,
            status=UpgradeStatus.PENDING_REVIEW,
            staged_path=str(staged),
            note="kurátorovaná verze",
        )
        db_session.add(uc)
        db_session.flush()

        t = client.get(f"/episodes/{ep.id}").text
        assert "Druhá verze — čeká na rozhodnutí" in t
        assert "kurátorovaná verze" in t
        assert f"/api/v1/upgrades/{uc.id}/audio" in t

    def test_candidate_audio_endpoint(self, db_session, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from audiobiblio.core.db.models import (
            Episode, Series, Station, Program, Work,
            UpgradeCandidate, UpgradeStatus,
        )
        from audiobiblio.web.routers.upgrades import router as upgrades_router
        from audiobiblio.web.deps import get_db

        st = Station(code="x1", name="X")
        db_session.add(st); db_session.flush()
        prog = Program(station_id=st.id, name="P")
        db_session.add(prog); db_session.flush()
        ser = Series(program_id=prog.id, name="S")
        db_session.add(ser); db_session.flush()
        w = Work(series_id=ser.id, title="W")
        db_session.add(w); db_session.flush()
        ep = Episode(work_id=w.id, title="E")
        db_session.add(ep); db_session.flush()
        staged = tmp_path / "c.m4a"
        staged.write_bytes(b"\x00" * 32)
        uc = UpgradeCandidate(
            episode_id=ep.id, candidate_url="file://x",
            status=UpgradeStatus.PENDING_REVIEW, staged_path=str(staged))
        db_session.add(uc); db_session.flush()

        app = FastAPI()
        app.include_router(upgrades_router)

        def _override():
            yield db_session

        app.dependency_overrides[get_db] = _override
        c = TestClient(app)
        assert c.get(f"/api/v1/upgrades/{uc.id}/audio").status_code == 200
        assert c.get("/api/v1/upgrades/99999/audio").status_code == 404


class TestInPlaceResolve:
    """file:// candidates (curated copy on a ro mount) resolve by LINKING —
    the candidate file is never moved, trashed, or tag-written."""

    def _mk(self, db_session, tmp_path):
        from audiobiblio.core.db.models import (
            Asset, AssetStatus, AssetType, Episode, Program, Series, Station,
            UpgradeCandidate, UpgradeStatus, Work,
        )
        st = Station(code="ip1", name="X")
        db_session.add(st); db_session.flush()
        prog = Program(station_id=st.id, name="P")
        db_session.add(prog); db_session.flush()
        ser = Series(program_id=prog.id, name="S")
        db_session.add(ser); db_session.flush()
        w = Work(series_id=ser.id, title="W")
        db_session.add(w); db_session.flush()
        ep = Episode(work_id=w.id, title="E")
        db_session.add(ep); db_session.flush()
        owned = tmp_path / "lib" / "owned.m4a"
        owned.parent.mkdir()
        owned.write_bytes(b"o" * 32)
        curated = tmp_path / "fiction" / "curated.m4a"
        curated.parent.mkdir()
        curated.write_bytes(b"c" * 32)
        asset = Asset(episode_id=ep.id, type=AssetType.AUDIO,
                      status=AssetStatus.COMPLETE, file_path=str(owned))
        db_session.add(asset); db_session.flush()
        uc = UpgradeCandidate(
            episode_id=ep.id, owned_asset_id=asset.id,
            candidate_url=f"file://{curated}",
            status=UpgradeStatus.PENDING_REVIEW, staged_path=str(curated))
        db_session.add(uc); db_session.flush()
        return asset, uc, owned, curated

    def _client(self, db_session, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from audiobiblio.web.routers.upgrades import router as upgrades_router
        from audiobiblio.web.deps import get_db
        import audiobiblio.web.routers.upgrades as upmod

        class _Cfg:
            library_dir = str(tmp_path / "lib")
        monkeypatch.setattr(upmod, "load_config", lambda: _Cfg())

        app = FastAPI()
        app.include_router(upgrades_router)

        def _override():
            yield db_session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    def test_replace_links_without_moving_candidate(
        self, db_session, tmp_path, monkeypatch
    ):
        asset, uc, owned, curated = self._mk(db_session, tmp_path)
        c = self._client(db_session, tmp_path, monkeypatch)

        r = c.post(f"/api/v1/upgrades/{uc.id}/resolve", json={"decision": "replace"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "replaced"
        db_session.refresh(asset)
        assert asset.file_path == str(curated)
        assert curated.exists(), "curated file must stay in place"
        assert not owned.exists(), "owned file goes to trash"

    def test_keep_old_never_trashes_in_place_candidate(
        self, db_session, tmp_path, monkeypatch
    ):
        asset, uc, owned, curated = self._mk(db_session, tmp_path)
        c = self._client(db_session, tmp_path, monkeypatch)

        r = c.post(f"/api/v1/upgrades/{uc.id}/resolve", json={"decision": "keep_old"})
        assert r.status_code == 200, r.text
        assert curated.exists(), "in-place candidate file is untouchable"
        assert owned.exists()


class TestWorkMetadata:
    """Book-level metadata: card on the page + PATCH with fan-out."""

    def _works_client(self, db_session):
        from audiobiblio.web.routers.works import router as works_router
        app = FastAPI()
        app.include_router(works_router)

        def _override():
            yield db_session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    def test_page_shows_metadata_card(self, client, book):
        t = client.get(f"/works/{book.id}").text
        assert "Metadata knihy" in t
        assert "Obohatit z databázeknih" in t
        assert 'id="bm-narrator"' in t

    def test_fanout_writes_manual_to_every_episode(self, db_session, book):
        from audiobiblio.core.db.models import MetadataValue
        c = self._works_client(db_session)
        r = c.patch(f"/api/v1/works/{book.id}/metadata",
                    json={"field": "narrator", "value": "Gustav Hašek"})
        assert r.status_code == 200, r.text
        assert r.json()["episodes_updated"] == 3
        rows = db_session.query(MetadataValue).filter_by(
            field="narrator", value="Gustav Hasek").all()  # unidecoded at the door
        assert len(rows) == 3
        assert all(v.entity_type == "episode" for v in rows)

    def test_work_fields_set_orm_and_provenance(self, db_session, book):
        from audiobiblio.core.db.models import MetadataValue, Work
        c = self._works_client(db_session)
        assert c.patch(f"/api/v1/works/{book.id}/metadata",
                       json={"field": "year", "value": "2025"}).status_code == 200
        assert c.patch(f"/api/v1/works/{book.id}/metadata",
                       json={"field": "publisher", "value": "audioteka"}).status_code == 200
        db_session.refresh(book)
        assert book.year == 2025
        pub = db_session.query(MetadataValue).filter_by(
            entity_type="work", entity_id=book.id, field="publisher").one()
        assert pub.value == "audioteka"

    def test_validation(self, db_session, book):
        c = self._works_client(db_session)
        assert c.patch(f"/api/v1/works/{book.id}/metadata",
                       json={"field": "bogus", "value": "x"}).status_code == 400
        assert c.patch(f"/api/v1/works/{book.id}/metadata",
                       json={"field": "year", "value": "brzy"}).status_code == 422
        assert c.patch("/api/v1/works/99999/metadata",
                       json={"field": "author", "value": "x"}).status_code == 404

    def test_expected_total_completeness_label(self, db_session, client, book):
        book.expected_total = 5
        db_session.flush()
        t = client.get(f"/works/{book.id}").text
        assert "2/5 dílů — chybí 3" in t


class TestWorkMetadataDiacritics:
    def test_values_are_unidecoded_at_the_door(self, db_session, book):
        """Binding rule: no Czech diacritics in tag-bound metadata."""
        from audiobiblio.core.db.models import MetadataValue
        from audiobiblio.web.routers.works import router as works_router
        app = FastAPI(); app.include_router(works_router)
        def _o():
            yield db_session
        app.dependency_overrides[get_db] = _o
        cl = TestClient(app)

        r = cl.patch(f"/api/v1/works/{book.id}/metadata",
                     json={"field": "narrator", "value": "Gustav Hašek"})
        assert r.json()["value"] == "Gustav Hasek"
        rows = db_session.query(MetadataValue).filter_by(field="narrator").all()
        assert all(v.value == "Gustav Hasek" for v in rows)

    def test_title_and_translator_editable(self, db_session, book):
        from audiobiblio.core.db.models import MetadataValue
        from audiobiblio.web.routers.works import router as works_router
        app = FastAPI(); app.include_router(works_router)
        def _o():
            yield db_session
        app.dependency_overrides[get_db] = _o
        cl = TestClient(app)

        assert cl.patch(f"/api/v1/works/{book.id}/metadata",
                        json={"field": "title", "value": "Oland - 01 Testovací"}).status_code == 200
        db_session.refresh(book)
        assert book.title == "Oland - 01 Testovaci"
        assert cl.patch(f"/api/v1/works/{book.id}/metadata",
                        json={"field": "translator", "value": "Martina Knapková"}).status_code == 200
        t = db_session.query(MetadataValue).filter_by(
            entity_type="work", field="translator").one()
        assert t.value == "Martina Knapkova"
