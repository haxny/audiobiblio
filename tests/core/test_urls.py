from audiobiblio.core.urls import norm_url, norm_url_strip_reair


def test_norm_url_lowercases_host_strips_slash():
    assert norm_url("https://MujRozhlas.CZ/podcast/") == "https://mujrozhlas.cz/podcast"


def test_norm_url_none():
    assert norm_url(None) == ""


def test_strip_reair_seven_digits():
    assert (
        norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2941669")
        == "https://mujrozhlas.cz/hra/osada"
    )


def test_strip_reair_keeps_short_suffix():
    assert (
        norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2")
        == "https://mujrozhlas.cz/hra/osada-2"
    )
