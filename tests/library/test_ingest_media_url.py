"""An ext_id match must not replace a page URL with a bare audio link."""
from __future__ import annotations

from audiobiblio.core.db.models import Episode
from audiobiblio.library.pipelines.ingest import _is_media_url, upsert_from_item

PAGE = "https://www.mujrozhlas.cz/cetba-na-pokracovani/kniha"
MP3 = "https://portal.rozhlas.cz/sites/default/files/audios/abc.mp3"


def _upsert(db, url):
    return upsert_from_item(db, url=url, item_title="Kniha", series_name="Četba",
                            author=None, uploader=None, program_name="Četba",
                            source_url="https://vltava.rozhlas.cz/cetba", ext_id="12131914")[0]


def test_audio_link_does_not_replace_page_url(db_session):
    ep = _upsert(db_session, PAGE)
    assert _upsert(db_session, MP3).id == ep.id
    assert db_session.get(Episode, ep.id).url == PAGE


def test_page_url_still_replaces_old_page_url(db_session):
    ep = _upsert(db_session, PAGE)
    _upsert(db_session, PAGE + "-novy")
    assert db_session.get(Episode, ep.id).url == PAGE + "-novy"


def test_media_url_detection():
    assert _is_media_url(MP3)
    assert _is_media_url("https://croaod.cz/stream/x.m4a/playlist.m3u8")
    assert not _is_media_url(PAGE)


def test_keyed_reair_revives_unkeyed_gone_stub(db_session):
    from audiobiblio.core.db.models import AvailabilityStatus
    stub = upsert_from_item(db_session, url="https://junior.rozhlas.cz/pernikova-pohadka-1234567",
                            item_title="Perníková pohádka. Veselý příběh z pohádkového království",
                            series_name="Velká pohádka", author=None, uploader=None,
                            program_name="Velká pohádka",
                            source_url="https://junior.rozhlas.cz/velka-pohadka-8049321")[0]
    stub.availability_status = AvailabilityStatus.GONE
    db_session.commit()
    ep = upsert_from_item(db_session, url="https://croaod.cz/stream/x.m4a/playlist.m3u8",
                          item_title="Perníková pohádka. Veselý příběh z pohádkového království",
                          series_name="Velká pohádka", author=None, uploader=None,
                          program_name="Velká pohádka", source_url="https://www.mujrozhlas.cz/velka-pohadka",
                          ext_id="11423690")[0]
    assert ep.id == stub.id
    assert ep.ext_id == "11423690"
    assert ep.availability_status == AvailabilityStatus.AVAILABLE
    assert ep.url.startswith("https://croaod.cz/")


def test_short_generic_title_never_merges(db_session):
    a = upsert_from_item(db_session, url="https://x.rozhlas.cz/zpravy-1", item_title="Zprávy",
                         series_name="S", author=None, uploader=None, program_name="P",
                         source_url="https://x.rozhlas.cz/p")[0]
    b = upsert_from_item(db_session, url="https://croaod.cz/y.m3u8", item_title="Zprávy",
                         series_name="S", author=None, uploader=None, program_name="P",
                         source_url="https://x.rozhlas.cz/p", ext_id="999")[0]
    assert a.id != b.id


def test_reair_prefers_the_already_downloaded_copy(db_session):
    from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, AvailabilityStatus
    title = "Houbové čarování. Pohádka o zakletých princích"
    kw = dict(item_title=title, series_name="Velká pohádka", author=None, uploader=None,
              program_name="Velká pohádka", source_url="https://junior.rozhlas.cz/velka-pohadka-1")
    stub = upsert_from_item(db_session, url="https://junior.rozhlas.cz/houbove-1", **kw)[0]
    stub.availability_status = AvailabilityStatus.GONE
    owned = upsert_from_item(db_session, url="https://croaod.cz/stream/old.m4a/playlist.m3u8", **kw)[0]
    db_session.add(Asset(episode_id=owned.id, type=AssetType.AUDIO,
                         status=AssetStatus.COMPLETE, file_path="/x.m4a"))
    db_session.commit()
    assert stub.id != owned.id
    ep = upsert_from_item(db_session, url="https://croaod.cz/stream/new.m4a/playlist.m3u8",
                          ext_id="11433689", **kw)[0]
    assert ep.id == owned.id and ep.ext_id == "11433689"
