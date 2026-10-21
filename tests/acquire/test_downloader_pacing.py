

class TestDirectMediaUrl:
    def test_hls_playlist_is_direct(self):
        from audiobiblio.acquire.downloader import _is_direct_media_url
        assert _is_direct_media_url(
            "https://croaod.cz/stream/a8e71582.m4a/playlist.m3u8")
        assert _is_direct_media_url(
            "https://croaod.cz/stream/a8e71582.m4a/manifest.mpd")
        assert _is_direct_media_url("https://aod.rozhlas.cz/x/123.mp3?x=1")

    def test_page_urls_are_not_direct(self):
        from audiobiblio.acquire.downloader import _is_direct_media_url
        assert not _is_direct_media_url("https://www.mujrozhlas.cz/cetba/x")
        assert not _is_direct_media_url(
            "https://budejovice.rozhlas.cz/letni-cteni-8130834")


def test_webpage_url_falls_back_to_article_alias():
    from types import SimpleNamespace
    from audiobiblio.acquire.downloader import _webpage_url
    ep = SimpleNamespace(
        url="https://croaod.cz/stream/x.m4a/playlist.m3u8",
        aliases=[SimpleNamespace(url="https://croaod.cz/stream/x.m4a/playlist.m3u8"),
                 SimpleNamespace(url="https://junior.rozhlas.cz/o-vetrnem-zamku-9311735")])
    assert _webpage_url(ep) == "https://junior.rozhlas.cz/o-vetrnem-zamku-9311735"
    assert _webpage_url(SimpleNamespace(url="https://www.mujrozhlas.cz/a/b", aliases=[])) \
        == "https://www.mujrozhlas.cz/a/b"
    assert _webpage_url(SimpleNamespace(url="https://portal.rozhlas.cz/sites/default/files/audios/a.mp3",
                                        aliases=[])) is None


def test_audio_only_episode_webpage_job_is_skipped_for_good(db_session, episode_factory, monkeypatch):
    """No page URL → job SKIPPED + asset SKIPPED, so the nightly planner
    never re-queues it (it used to re-error ~1300 jobs every night)."""
    from audiobiblio.acquire import downloader
    from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, DownloadJob, JobStatus
    from audiobiblio.library.pipelines.checks import plan_downloads
    ep = episode_factory()
    ep.url = "https://portal.rozhlas.cz/sites/default/files/audios/a.mp3"
    db_session.commit()
    job = DownloadJob(episode_id=ep.id, asset_type=AssetType.WEBPAGE, status=JobStatus.PENDING)
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(downloader, "get_session", lambda: db_session)
    downloader.run_pending_jobs()
    assert job.status == JobStatus.SKIPPED
    asset = db_session.query(Asset).filter_by(episode_id=ep.id, type=AssetType.WEBPAGE).one()
    assert asset.status == AssetStatus.SKIPPED
    replanned = plan_downloads(db_session, ep.id)
    assert AssetType.WEBPAGE not in {j.asset_type for j in replanned}
