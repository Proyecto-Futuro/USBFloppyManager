"""Copia por lotes (equivalente a GotekTool.sh) con 4 modos.

La vista previa y la copia real ejecutan EXACTAMENTE el mismo código sobre imágenes FAT12 en memoria
(la vista previa rellena con ceros y no toca el dispositivo), así que preview == resultado."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .core import (DATA_CLUSTERS, SECTOR, Device, DiskFull, FatImage, GotekError, SLOT_SIZE,
                   make_blank_image)
from .names import is_short_name

MODES = {
    1: "Aplanar: todos los archivos en la raíz de cada disquete",
    2: "Aplanar, pero cambiar de disquete en cada directorio",
    3: "Conservar la estructura de directorios",
    4: "Conservar estructura; cada carpeta raíz va a un disquete distinto",
}

MAX_FILE = DATA_CLUSTERS * SECTOR


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


@dataclass
class Placement:
    floppy: int
    number: int          # nº de archivo dentro del disquete (1..n)
    src: str
    dest: str            # ruta final dentro del disquete (ya con 8.3/alias aplicado)
    size: int
    status: str = "ok"   # ok | too_big | renamed
    long_name: bool = False   # el nombre final no es 8.3 (usará entradas LFN)


@dataclass
class BatchResult:
    placements: List[Placement] = field(default_factory=list)
    first: int = 0
    last: int = 0
    cancelled: bool = False

    @property
    def skipped(self):
        return [p for p in self.placements if p.status == "too_big"]

    @property
    def long_names(self):
        return [p for p in self.placements if p.long_name and p.status != "too_big"]

    def contents_text(self) -> str:
        out, prev = [], None
        for p in self.placements:
            if p.status == "too_big":
                continue
            if prev is not None and p.floppy != prev:
                out.append("")
            out.append(f"{p.floppy}\t{p.number}\t{p.src}")
            prev = p.floppy
        return "\n".join(out) + "\n"


class _Floppy:
    def __init__(self, n: int, short_names: bool):
        self.n = n
        self.img = FatImage(make_blank_image(f"FD{n:03d}"), short_names=short_names)

    def try_add(self, dest: str, size: int, src: str, real: bool):
        """Devuelve la ruta final o None si no cabe."""
        if size > MAX_FILE:
            return None
        data = _read(src) if real else bytes(size)
        try:
            return self.img.write("/" + dest, data)
        except DiskFull:
            return None

    def finish(self, dev: Optional[Device]):
        if dev is not None:
            dev.write_slot(self.n, self.img.image())
        self.img.close()


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise GotekError(f"No se pudo leer {path}: {e}") from e


def list_files(srcdir: str) -> List[str]:
    out = []
    for root, _, files in os.walk(srcdir):
        for f in files:
            out.append(os.path.join(root, f))
    out.sort(key=natural_key)
    return out


def run_batch(srcdir: str, start: int, mode: int, dev: Optional[Device] = None,
              progress: Optional[Callable[[Placement, int, int], None]] = None,
              short_names: bool = False,
              cancel: Optional[Callable[[], bool]] = None,
              max_slot: Optional[int] = None) -> BatchResult:
    """dev=None => vista previa (misma lógica, sin leer ni escribir). Con dev => copia real.
    `progress(placement, hechos, total)`; `cancel()` -> True detiene tras el archivo actual."""
    if mode not in MODES:
        raise GotekError(f"Modo inválido: {mode}")
    srcdir = os.path.normpath(srcdir)
    if not os.path.isdir(srcdir):
        raise GotekError(f"No existe el directorio: {srcdir}")
    if start < 0:
        raise GotekError("El slot inicial no puede ser negativo.")
    limit = dev.n_slots if dev is not None else max_slot

    res = BatchResult(first=start)
    cur = start
    real = dev is not None

    def open_floppy(n):
        if limit is not None and n >= limit:
            raise GotekError(f"El USB no tiene más slots (se necesita el {n}, máximo {limit - 1}).")
        return _Floppy(n, short_names)

    floppy = open_floppy(cur)
    count, last_flip, names_here = 0, None, set()

    def next_floppy():
        nonlocal cur, floppy, count, names_here
        floppy.finish(dev)
        cur += 1
        floppy = open_floppy(cur)
        count, names_here = 0, set()

    files = list_files(srcdir)
    try:
        for i, src in enumerate(files, 1):
            if cancel and cancel():
                res.cancelled = True
                break
            rel = os.path.relpath(src, srcdir).replace(os.sep, "/")
            reldir, name = (rel.rsplit("/", 1) if "/" in rel else ("", rel))
            flip, dest_dir = None, reldir
            if mode == 2:
                flip = reldir
            elif mode == 4:
                flip = reldir.split("/")[0] if reldir else ""
                dest_dir = reldir.split("/", 1)[1] if "/" in reldir else ""
            if mode in (2, 4) and count > 0 and flip != last_flip:
                next_floppy()
            last_flip = flip

            if mode in (1, 2):
                dest = name
            else:
                dest = f"{dest_dir}/{name}" if dest_dir else name
            status = "ok"
            if dest.lower() in names_here:  # colisión (FAT no distingue mayúsculas): renombrar
                stem, ext = os.path.splitext(name)
                k = 2
                while True:
                    cand = f"{stem}_{k}{ext}"
                    cdest = f"{dest_dir}/{cand}" if (dest_dir and mode in (3, 4)) else cand
                    if cdest.lower() not in names_here:
                        break
                    k += 1
                dest, status = cdest, "renamed"

            size = os.path.getsize(src)
            final = floppy.try_add(dest, size, src, real)
            if final is None and count > 0 and size <= MAX_FILE:
                next_floppy()
                final = floppy.try_add(dest, size, src, real)
            if final is None:
                pl = Placement(cur, 0, src, dest, size, "too_big")
            else:
                count += 1
                names_here.add(dest.lower())
                pl = Placement(cur, count, src, final.lstrip("/"), size, status,
                               long_name=not all(is_short_name(p) for p in final.split("/") if p))
            res.placements.append(pl)
            if progress:
                progress(pl, i, len(files))
        floppy.finish(dev)
    except BaseException:
        floppy.img.close()
        raise
    res.last = cur
    return res


def write_contents(res: BatchResult, outdir: str = ".") -> str:
    path = os.path.join(outdir, f"Contents {res.first:03d}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(res.contents_text())
    return path
