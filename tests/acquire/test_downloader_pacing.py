

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
