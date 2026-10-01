import os
import shutil
import subprocess

import pytest

from usbfloppymanager.core import SLOT_SIZE, STRIDE

HAVE_FSCK = shutil.which("fsck.fat") is not None
HAVE_MDIR = shutil.which("mdir") is not None


def make_image(path, n_slots=8, fill=b"\xAA"):
    """Imagen de USB sintética: huecos y slots rellenos con un patrón para detectar escrituras ajenas."""
    size = (n_slots - 1) * STRIDE + SLOT_SIZE
    with open(path, "wb") as f:
        f.write(fill * size)
    return str(path)


@pytest.fixture
def img(tmp_path):
    return make_image(tmp_path / "usb.img", 8)


@pytest.fixture
def formatted(tmp_path):
    from usbfloppymanager.core import Device
    p = make_image(tmp_path / "usb.img", 8)
    with Device(p, writable=True) as d:
        for n in range(d.n_slots):
            d.format_slot(n, f"FD{n:03d}")
    return p


def fsck(slot_bytes, tmp_path, name="s.img"):
    """Devuelve (código, salida) de fsck.fat -n sobre un slot."""
    p = tmp_path / name
    p.write_bytes(slot_bytes)
    r = subprocess.run(["fsck.fat", "-n", str(p)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def mdir(slot_bytes, tmp_path, name="m.img", recurse=True):
    p = tmp_path / name
    p.write_bytes(slot_bytes)
    return subprocess.run(["mdir"] + (["-/"] if recurse else []) + ["-i", str(p), "::"], capture_output=True, text=True).stdout


def make_tree(root, spec):
    """spec: {ruta_relativa: bytes|int}"""
    for rel, content in spec.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content if isinstance(content, bytes) else os.urandom(content))
    return str(root)
