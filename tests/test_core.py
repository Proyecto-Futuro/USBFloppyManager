import pytest

from usbfloppymanager.core import (SLOT_SIZE, STRIDE, Device, DiskFull, FatImage, GotekError, Slot,
                                   backup_device, export_image, extract_tree, get_label_bytes,
                                   import_image, is_valid_fat12, make_blank_image, restore_device,
                                   set_label_bytes)
from conftest import HAVE_FSCK, HAVE_MDIR, fsck, make_image, mdir

GAP = SLOT_SIZE  # el hueco es [n*STRIDE+SLOT_SIZE, (n+1)*STRIDE)


def raw(path):
    with open(path, "rb") as f:
        return f.read()


def test_geometry_slot_count(img):
    with Device(img) as d:
        assert d.n_slots == 8
    assert STRIDE - SLOT_SIZE == 96 * 1024


def test_blank_image_valid():
    data = make_blank_image("HOLA")
    assert len(data) == SLOT_SIZE and is_valid_fat12(data)
    assert get_label_bytes(data) == "HOLA"
    assert not is_valid_fat12(b"\xAA" * SLOT_SIZE)


@pytest.mark.skipif(not HAVE_FSCK, reason="sin fsck.fat")
def test_blank_image_fsck_clean(tmp_path):
    code, out = fsck(make_blank_image("LIMPIO"), tmp_path)
    assert code == 0, out


def test_write_read_roundtrip_bytes(formatted):
    payload = bytes(range(256)) * 400
    with Device(formatted, writable=True) as d, Slot(d, 3) as s:
        s.write("/dir/sub/DATA.BIN", payload)
    with Device(formatted) as d, Slot(d, 3) as s:
        assert s.read("/DIR/SUB/DATA.BIN") == payload


def test_roundtrip_exact_names_uppercased(formatted):
    payload = b"hola" * 1000
    with Device(formatted, writable=True) as d, Slot(d, 1) as s:
        final = s.write("/carpeta/prueba.txt", payload)
        assert final == "/CARPETA/PRUEBA.TXT"
    with Device(formatted) as d, Slot(d, 1) as s:
        assert s.read("/CARPETA/PRUEBA.TXT") == payload


def test_write_does_not_touch_gap_or_other_slots(formatted):
    before = raw(formatted)
    with Device(formatted, writable=True) as d, Slot(d, 4) as s:
        s.write("/A.TXT", b"x" * 100000)
    after = raw(formatted)
    assert len(before) == len(after)
    lo, hi = 4 * STRIDE, 4 * STRIDE + SLOT_SIZE
    assert before[:lo] == after[:lo]
    assert before[hi:] == after[hi:]
    assert before[lo:hi] != after[lo:hi]


def test_gap_preserved_on_format(img):
    before = raw(img)
    with Device(img, writable=True) as d:
        d.format_slot(2, "X")
    after = raw(img)
    lo, hi = 2 * STRIDE, 2 * STRIDE + SLOT_SIZE
    assert before[:lo] == after[:lo] and before[hi:] == after[hi:]


def test_write_slot_validation(formatted):
    with Device(formatted, writable=True) as d:
        with pytest.raises(GotekError):
            d.write_slot(0, b"corto")
        with pytest.raises(GotekError):
            d.write_slot(99, make_blank_image())
    with Device(formatted) as d:
        with pytest.raises(GotekError):
            d.write_slot(0, make_blank_image())


def test_slot_requires_valid_fat(img):
    with Device(img) as d:
        with pytest.raises(GotekError):
            Slot(d, 0)
        assert not d.slot_info(0).valid


def test_disk_full_is_clean(tmp_path):
    img = FatImage(make_blank_image("LLENO"))
    img.write("/A.BIN", b"x" * 1_000_000)
    with pytest.raises(DiskFull):
        img.write("/NEW/B.BIN", b"y" * 1_000_000)
    assert not img.exists("/NEW")
    assert [e.path for e in img.walk()] == ["/A.BIN"]
    if HAVE_FSCK:
        code, out = fsck(img.image(), tmp_path)
        assert code == 0, out


def test_root_entries_exhausted(tmp_path):
    img = FatImage(make_blank_image("ROOT"))
    n = 0
    with pytest.raises(DiskFull):
        for i in range(400):
            img.write(f"/F{i:04d}.TXT", b"x")
            n += 1
    assert n == 223  # 224 entradas - la de etiqueta de volumen
    if HAVE_FSCK:
        code, out = fsck(img.image(), tmp_path)
        assert code == 0, out


def test_remove_and_free_space(formatted):
    with Device(formatted, writable=True) as d, Slot(d, 0) as s:
        free0 = s.free_bytes()
        s.write("/D/A.BIN", b"z" * 50000)
        assert s.free_bytes() < free0
        s.remove("/D")
        assert s.free_bytes() == free0


def test_special_names(tmp_path):
    img = FatImage(make_blank_image("N"))
    names = ["con espacios.txt", "acentuación ñandú.txt", "Mayúsculas Y minúsculas.DAT"]
    for n in names:
        img.write("/" + n, n.encode())
    for n in names:
        assert img.read("/" + n) == n.encode()
    if HAVE_FSCK:
        code, out = fsck(img.image(), tmp_path)
        assert code == 0, out


def test_label_change(formatted, tmp_path):
    with Device(formatted, writable=True) as d:
        d.set_label(2, "nuevo")
    with Device(formatted) as d:
        assert d.slot_info(2).label == "NUEVO"
        data = d.read_slot(2)
    assert data[43:54] == b"NUEVO      "
    if HAVE_MDIR:
        assert "is NUEVO" in mdir(data, tmp_path, recurse=False)
    if HAVE_FSCK:
        assert fsck(data, tmp_path)[0] == 0


def test_label_keeps_files_and_validates(formatted):
    with Device(formatted, writable=True) as d:
        with Slot(d, 0) as s:
            s.write("/A.TXT", b"1")
        d.set_label(0, "OTRA")
        with Slot(d, 0) as s:
            assert s.read("/A.TXT") == b"1"
        with pytest.raises(GotekError):
            d.set_label(0, "demasiado larga etiqueta")
        with pytest.raises(GotekError):
            d.set_label(0, "a*b")


def test_label_idempotent_bytes():
    a = set_label_bytes(make_blank_image("A"), "B")
    assert set_label_bytes(a, "B") == a
    assert get_label_bytes(a) == "B"


def test_export_import(formatted, tmp_path):
    with Device(formatted, writable=True) as d, Slot(d, 1) as s:
        s.write("/X.TXT", b"datos")
    out = tmp_path / "s1.img"
    with Device(formatted) as d:
        export_image(d, 1, str(out))
    assert out.stat().st_size == SLOT_SIZE
    with Device(formatted, writable=True) as d:
        import_image(d, 5, str(out))
        with Slot(d, 5) as s:
            assert s.read("/X.TXT") == b"datos"
        bad = tmp_path / "bad.img"
        bad.write_bytes(b"\x00" * SLOT_SIZE)
        with pytest.raises(GotekError):
            import_image(d, 5, str(bad))


def test_backup_restore(formatted, tmp_path):
    with Device(formatted, writable=True) as d, Slot(d, 6) as s:
        s.write("/Z.TXT", b"zeta")
    bk = tmp_path / "bk.img"
    with Device(formatted) as d:
        backup_device(d, str(bk))
    other = make_image(tmp_path / "otro.img", 8)
    with Device(other, writable=True) as d:
        restore_device(d, str(bk))
        with Slot(d, 6) as s:
            assert s.read("/Z.TXT") == b"zeta"


def test_extract_tree(formatted, tmp_path):
    with Device(formatted, writable=True) as d, Slot(d, 0) as s:
        s.write("/D/E/F.TXT", b"f")
        s.write("/D/G.TXT", b"g")
    out = tmp_path / "out"
    with Device(formatted) as d, Slot(d, 0) as s:
        files = extract_tree(s, "/D", str(out))
    assert len(files) == 2
    assert (out / "D" / "E" / "F.TXT").read_bytes() == b"f"


def test_readonly_slot_rejects_write(formatted):
    with Device(formatted) as d, Slot(d, 0) as s:
        with pytest.raises(GotekError):
            s.write("/A.TXT", b"x")
