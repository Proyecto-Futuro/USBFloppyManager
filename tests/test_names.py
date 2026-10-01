from usbfloppymanager.names import clean_name, is_short_name, to_short_name


def test_is_short_name():
    for ok in ("A.TXT", "readme.txt", "FILE10.BIN", "NOEXT", "A~1.TXT"):
        assert is_short_name(ok), ok
    for bad in ("with space.txt", "muylargonombre.txt", "a.html", "ñandú.txt", "a..b", ".", ".."):
        assert not is_short_name(bad), bad


def test_to_short_name_basic():
    assert to_short_name("readme.txt") == "README.TXT"
    assert to_short_name("Documento largo.html") == "DOCUME~1.HTM"
    assert to_short_name("Documento largo.html", ["DOCUME~1.HTM"]) == "DOCUME~2.HTM"
    assert to_short_name("acentuación.txt") == "ACENTU~1.TXT"


def test_to_short_name_unique_many():
    taken = []
    for _ in range(15):
        taken.append(to_short_name("nombre muy largo.txt", taken))
    assert len(set(taken)) == 15
    assert all(is_short_name(t) and len(t.split(".")[0]) <= 8 for t in taken)


def test_clean_name():
    assert clean_name('a:b*c?.txt') == "a_b_c_.txt"
    assert clean_name("fin.. ") == "fin"
    assert clean_name("") == "_"
