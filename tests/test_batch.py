import os

import pytest

from usbfloppymanager.batch import MAX_FILE, MODES, natural_key, run_batch, write_contents
from usbfloppymanager.core import SLOT_SIZE, STRIDE, Device, FatImage, Slot, make_blank_image
from conftest import HAVE_FSCK, HAVE_MDIR, fsck, make_image, make_tree, mdir

TREE = {
    "file1.txt": 100, "file2.txt": 100, "file10.txt": 100,
    "a/x.txt": 50, "a/y.txt": 50,
    "a/deep/z.txt": 50,
    "b/x.txt": 70,
    "b/c/w.txt": 20,
}


@pytest.fixture
def tree(tmp_path):
    return make_tree(tmp_path / "src", TREE)


def layout(res):
    out = {}
    for p in res.placements:
        out.setdefault(p.floppy, []).append(p.dest.lower())
    return out


def test_natural_sort():
    names = ["f10", "f2", "f1"]
    assert sorted(names, key=natural_key) == ["f1", "f2", "f10"]


def test_mode1_flatten_with_collisions(tree):
    res = run_batch(tree, 0, 1)
    dests = layout(res)[0]
    assert len(dests) == len(TREE) and len(set(d.lower() for d in dests)) == len(dests)
    assert any(p.status == "renamed" for p in res.placements)  # x.txt repetido
    assert "x_2.txt" in dests


def test_mode2_new_floppy_per_directory(tree):
    res = run_batch(tree, 3, 2)
    assert res.first == 3
    flop = layout(res)
    # orden natural de rutas: a/deep, a, b/c, b, raíz => 5 disquetes
    assert sorted(flop) == [3, 4, 5, 6, 7]
    assert flop[3] == ["z.txt"] and flop[7] == ["file1.txt", "file2.txt", "file10.txt"]


def test_mode3_keeps_structure(tree):
    res = run_batch(tree, 0, 3)
    assert set(layout(res)[0]) == set(TREE)
    assert res.last == 0


def test_mode4_root_folder_per_floppy(tree):
    res = run_batch(tree, 0, 4)
    flop = layout(res)
    assert flop[2] == ["file1.txt", "file2.txt", "file10.txt"]
    assert sorted(flop[0]) == ["deep/z.txt", "x.txt", "y.txt"]
    assert sorted(flop[1]) == ["c/w.txt", "x.txt"]


def test_preview_equals_real(tree, img):
    pre = run_batch(tree, 1, 3, short_names=True)
    with Device(img, writable=True) as d:
        real = run_batch(tree, 1, 3, d, short_names=True)
    assert [(p.floppy, p.number, p.dest, p.status) for p in pre.placements] == \
           [(p.floppy, p.number, p.dest, p.status) for p in real.placements]


def test_real_copy_content_and_fsck(tree, img, tmp_path):
    with Device(img, writable=True) as d:
        res = run_batch(tree, 0, 3, d)
    with Device(img) as d:
        with Slot(d, 0) as s:
            names = {e.path for e in s.walk() if not e.is_dir}
        data = d.read_slot(0)
    assert len(names) == len(TREE)
    if HAVE_FSCK:
        code, out = fsck(data, tmp_path)
        assert code == 0, out
    if HAVE_MDIR:
        listing = mdir(data, tmp_path)
        assert "FILE10" in listing and "DEEP" in listing.upper()
    # bytes idénticos
    with Device(img) as d, Slot(d, 0) as s:
        with open(os.path.join(tree, "a", "deep", "z.txt"), "rb") as f:
            assert s.read("/A/DEEP/Z.TXT") == f.read()


def test_no_write_outside_target_slots(tree, img):
    with open(img, "rb") as f:
        before = f.read()
    with Device(img, writable=True) as d:
        res = run_batch(tree, 2, 2, d)
    with open(img, "rb") as f:
        after = f.read()
    used = range(res.first, res.last + 1)
    keep = bytearray(after)
    for n in used:
        keep[n * STRIDE:n * STRIDE + SLOT_SIZE] = before[n * STRIDE:n * STRIDE + SLOT_SIZE]
    assert bytes(keep) == before  # nada fuera de los slots usados, y el hueco de 96 KiB intacto
    for n in used:
        assert after[n * STRIDE + SLOT_SIZE:(n + 1) * STRIDE] == before[n * STRIDE + SLOT_SIZE:(n + 1) * STRIDE]


def test_file_too_big_skipped(tmp_path):
    src = make_tree(tmp_path / "s", {"big.bin": MAX_FILE + 1, "ok.bin": 10})
    res = run_batch(src, 0, 1)
    assert [os.path.basename(p.src) for p in res.skipped] == ["big.bin"]
    assert res.last == 0 and res.placements[1].floppy == 0


def test_files_overflow_to_next_floppy(tmp_path):
    spec = {f"f{i}.bin": 600_000 for i in range(5)}
    res = run_batch(make_tree(tmp_path / "s", spec), 0, 1)
    flop = layout(res)
    assert len(flop) == 3 and [len(v) for v in flop.values()] == [2, 2, 1]


def test_many_small_files_exhaust_root(tmp_path):
    spec = {f"f{i:03d}.txt": b"x" for i in range(300)}
    src = make_tree(tmp_path / "s", spec)
    res = run_batch(src, 0, 1)
    flop = layout(res)
    assert sorted(flop) == [0, 1] and len(flop[0]) == 223 and len(flop[1]) == 77


def test_weird_names(tmp_path, img):
    spec = {"con espacios.txt": b"1", "acentuación ñandú.txt": b"2", "Mayús/Sub carpeta/ÁÉÍ.txt": b"3"}
    src = make_tree(tmp_path / "s", spec)
    for short in (False, True):
        with Device(img, writable=True) as d:
            res = run_batch(src, 0, 3, d, short_names=short)
        assert not res.skipped
        with Device(img) as d:
            data = d.read_slot(0)
        if HAVE_FSCK:
            assert fsck(data, tmp_path)[0] == 0
        if short:
            assert all(not p.long_name for p in res.placements)
            assert not res.long_names
        else:
            assert len(res.long_names) == 3


def test_short_names_unique(tmp_path):
    spec = {f"documento largo {i}.txt": b"x" for i in range(12)}
    res = run_batch(make_tree(tmp_path / "s", spec), 0, 1, short_names=True)
    dests = [p.dest for p in res.placements]
    assert len(set(dests)) == 12 and all(len(d.split(".")[0]) <= 8 for d in dests)


def test_case_insensitive_collision(tmp_path):
    src = make_tree(tmp_path / "s", {"a/Foo.txt": b"1", "b/foo.TXT": b"2"})
    res = run_batch(src, 0, 1)
    assert not res.skipped and len({p.dest.lower() for p in res.placements}) == 2


def test_not_enough_slots(tree, tmp_path):
    from usbfloppymanager.core import GotekError
    small = make_image(tmp_path / "s.img", 2)
    with Device(small, writable=True) as d:
        with pytest.raises(GotekError):
            run_batch(tree, 0, 2, d)


def test_cancel(tree, img):
    with Device(img, writable=True) as d:
        calls = []
        res = run_batch(tree, 0, 1, d, cancel=lambda: len(calls.append(1) or calls) > 2)
    assert res.cancelled and len(res.placements) == 2


def test_contents_file(tree, tmp_path):
    res = run_batch(tree, 5, 4)
    path = write_contents(res, str(tmp_path))
    assert path.endswith("Contents 005.txt")
    text = open(path, encoding="utf-8").read()
    assert text.splitlines()[0].startswith("5\t1\t")
    assert "" in text.splitlines()  # línea en blanco entre disquetes


def test_invalid_args(tmp_path):
    from usbfloppymanager.core import GotekError
    with pytest.raises(GotekError):
        run_batch(str(tmp_path), 0, 9)
    with pytest.raises(GotekError):
        run_batch(str(tmp_path / "no"), 0, 1)
