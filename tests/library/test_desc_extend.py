from audiobiblio.library.desc_extend import extend_with_missing_body

OLD = "Autor: Titul\n\nPerex.\n\nPrvni odstavec.\n\nUcinkuje: X\n\nhttps://u"
BODY = ["Prvni odstavec.", "Druhy odstavec.", "Treti odstavec."]


def test_missing_paragraphs_go_right_after_the_first():
    new = extend_with_missing_body(OLD, BODY)
    assert new == ("Autor: Titul\n\nPerex.\n\nPrvni odstavec.\n\nDruhy odstavec.\n\n"
                   "Treti odstavec.\n\nUcinkuje: X\n\nhttps://u")


def test_already_complete_or_single_paragraph_gives_none():
    full = extend_with_missing_body(OLD, BODY)
    assert extend_with_missing_body(full, BODY) is None
    assert extend_with_missing_body(OLD, BODY[:1]) is None


def test_rewritten_description_is_left_alone():
    assert extend_with_missing_body("Vlastni popis od uzivatele.", BODY) is None
    assert extend_with_missing_body(None, BODY) is None
