

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
