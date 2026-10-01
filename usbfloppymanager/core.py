"""Núcleo: acceso a slots de 1440 KiB separados 1536 KiB dentro de un USB/imagen,
formateo FAT12 y operaciones de archivos. No usa `mount`: acceso directo al dispositivo.

Los slots se manipulan en memoria (`FatImage`); `Slot` los vuelca al dispositivo con commit."""
from __future__ import annotations

import io
import os
import struct
import warnings
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from pyfatfs.PyFatFS import PyFatBytesIOFS

from .names import clean_name, is_short_name, to_short_name

STRIDE = 1536 * 1024      # separación entre disquetes en el USB
SLOT_SIZE = 1440 * 1024   # tamaño útil de cada disquete
SECTOR = 512
ROOT_START = (1 + 2 * 9) * SECTOR    # la raíz empieza tras boot + 2 FAT
ROOT_ENTRIES = 224
DATA_CLUSTERS = 2847
LABEL_FORBIDDEN = '"*+,./:;<=>?[\\]|'


class GotekError(Exception):
    pass


class DiskFull(GotekError):
    pass


# ---------------------------------------------------------------- etiqueta
def normalize_label(label: str) -> bytes:
    label = (label or "NO NAME").upper().strip()
    bad = sorted({c for c in label if c in LABEL_FORBIDDEN or ord(c) < 32})
    if bad:
        raise GotekError(f"La etiqueta contiene caracteres no válidos: {' '.join(bad)}")
    raw = label.encode("cp437", "replace")
    if len(raw) > 11:
        raise GotekError("La etiqueta admite como máximo 11 caracteres.")
    return raw.ljust(11)


def _volume_entry(raw_label: bytes) -> bytes:
    e = bytearray(32)
    e[0:11] = raw_label
    e[11] = 0x08
    struct.pack_into("<HHHHHHHI", e, 12, 0, 0, 0, 0, 0, 0x4C21, 0, 0)  # fecha/hora fijas
    return bytes(e)


def set_label_bytes(data: bytes, label: str) -> bytes:
    """Cambia la etiqueta en el boot sector y en la entrada de volumen de la raíz."""
    raw = normalize_label(label)
    img = bytearray(data)
    img[43:54] = raw
    free = None
    for i in range(ROOT_ENTRIES):
        off = ROOT_START + i * 32
        first, attr = img[off], img[off + 11]
        if first == 0x00:
            free = off if free is None else free
            break
        if first == 0xE5:
            free = off if free is None else free
            continue
        if attr != 0x0F and attr & 0x08 and not attr & 0x10:
            img[off:off + 32] = _volume_entry(raw)
            return bytes(img)
    if free is None:
        raise GotekError("La raíz está llena: no hay hueco para la entrada de etiqueta.")
    img[free:free + 32] = _volume_entry(raw)
    return bytes(img)


def get_label_bytes(data: bytes) -> str:
    for i in range(ROOT_ENTRIES):
        off = ROOT_START + i * 32
        first, attr = data[off], data[off + 11]
        if first == 0x00:
            break
        if first != 0xE5 and attr != 0x0F and attr & 0x08 and not attr & 0x10:
            return data[off:off + 11].decode("cp437", "replace").strip()
    return data[43:54].decode("cp437", "replace").strip()


# ---------------------------------------------------------------- formateo
def make_blank_image(label: str = "NO NAME") -> bytes:
    """Imagen FAT12 vacía de 1,44 MB (misma geometría que un disquete real)."""
    boot = bytearray(SECTOR)
    boot[0:3] = b"\xEB\x3C\x90"
    boot[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", boot, 11,
                     512,    # bytes/sector
                     1,      # sectores/cluster
                     1,      # reservados
                     2,      # nº FAT
                     ROOT_ENTRIES,
                     2880,   # sectores totales
                     0xF0,   # media
                     9,      # sectores/FAT
                     18,     # sectores/pista
                     2,      # cabezas
                     0, 0)
    struct.pack_into("<BBBI", boot, 36, 0, 0, 0x29, 0x1234ABCD)
    boot[54:62] = b"FAT12   "
    boot[510:512] = b"\x55\xAA"
    img = bytearray(SLOT_SIZE)
    img[0:SECTOR] = boot
    for fat in range(2):  # dos copias de la FAT
        off = SECTOR * (1 + fat * 9)
        img[off:off + 3] = b"\xF0\xFF\xFF"
    return set_label_bytes(bytes(img), label)


def is_valid_fat12(data: bytes) -> bool:
    if len(data) < SECTOR or data[510:512] != b"\x55\xAA":
        return False
    bps, spc, rsv, nfat, root, tot16 = struct.unpack_from("<HBHBHH", data, 11)
    return bps == 512 and spc in (1, 2, 4) and nfat >= 1 and root > 0 and 0 < tot16 <= 2880


# ---------------------------------------------------------------- dispositivo
class Device:
    """USB (dispositivo en crudo) o archivo de imagen. Lee/escribe slots completos,
    siempre alineados (offsets y longitudes múltiplos de 4 KiB, válido para dispositivos
    en crudo de Windows). Con `writable=True` se aplican las comprobaciones de seguridad
    de `devices.check_writable_target` (sólo extraíbles; nunca el disco del sistema)."""

    def __init__(self, path: str, writable: bool = False, slots: Optional[int] = None,
                 force: bool = False):
        from . import devices  # import tardío: evita ciclo
        self.path = path
        self.writable = writable
        self.force = force
        self._winraw = None
        if writable:
            devices.check_writable_target(path, force)
        try:
            if devices.is_windows_raw_path(path):
                self._winraw = devices.open_windows_raw(path, writable)
                self._f = self._winraw
            else:
                self._f = open(path, "r+b" if writable else "rb", buffering=0)
        except PermissionError as e:
            raise GotekError(devices.permission_hint(path)) from e
        except OSError as e:
            if getattr(e, "winerror", None) == 5 or e.errno in (1, 13):
                raise GotekError(devices.permission_hint(path)) from e
            raise GotekError(f"No se puede abrir {path}: {e}") from e
        self.size = self._detect_size()
        natural = ((self.size + STRIDE - SLOT_SIZE) // STRIDE if self.size >= SLOT_SIZE else 0) if self.size else None
        if slots is not None:
            self.n_slots = min(slots, natural) if natural is not None else slots  # nunca más de los que caben
        elif natural is not None:
            self.n_slots = natural
        else:
            self.close()
            raise GotekError("No se pudo detectar el tamaño del dispositivo; indica el nº de slots.")

    def _detect_size(self) -> int:
        if self._winraw is not None:
            return self._winraw.size
        try:
            return self._f.seek(0, os.SEEK_END)
        except OSError:
            return 0

    def close(self):
        try:
            self._f.close()
        finally:
            self._winraw = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _check(self, n: int):
        if not 0 <= n < self.n_slots:
            raise GotekError(f"Slot {n} fuera de rango (0..{self.n_slots - 1}).")

    def read_slot(self, n: int) -> bytes:
        self._check(n)
        self._f.seek(n * STRIDE)
        data = self._f.read(SLOT_SIZE)
        return data.ljust(SLOT_SIZE, b"\x00")

    def write_slot(self, n: int, data: bytes):
        """Escribe exactamente [n*STRIDE, n*STRIDE+SLOT_SIZE); el hueco de 96 KiB nunca se toca."""
        if not self.writable:
            raise GotekError("Dispositivo abierto en solo lectura.")
        if len(data) != SLOT_SIZE:
            raise GotekError(f"Un slot debe tener {SLOT_SIZE} bytes (recibidos {len(data)}).")
        self._check(n)
        self._f.seek(n * STRIDE)
        self._f.write(data)
        try:
            os.fsync(self._f.fileno())
        except (OSError, ValueError):
            pass

    def format_slot(self, n: int, label: str = "NO NAME"):
        self.write_slot(n, make_blank_image(label))

    def set_label(self, n: int, label: str):
        data = self.read_slot(n)
        if not is_valid_fat12(data):
            raise GotekError(f"El slot {n} no contiene un FAT12 válido (fórmatealo primero).")
        self.write_slot(n, set_label_bytes(data, label))

    def slot_info(self, n: int) -> "SlotInfo":
        data = self.read_slot(n)
        if not is_valid_fat12(data):
            return SlotInfo(n, False, "", 0, 0, 0)
        try:
            with FatImage(data, writable=False) as s:
                files, _ = s.stats()
                free = s.free_bytes()
        except Exception:
            return SlotInfo(n, False, "", 0, 0, 0)
        total = DATA_CLUSTERS * SECTOR
        return SlotInfo(n, True, get_label_bytes(data), files, total - free, total)

    def iter_info(self) -> Iterator["SlotInfo"]:
        for n in range(self.n_slots):
            yield self.slot_info(n)


@dataclass
class SlotInfo:
    index: int
    valid: bool
    label: str
    files: int
    used_bytes: int       # ocupación real de la zona de datos (clusters, incl. carpetas)
    total_bytes: int      # capacidad de la zona de datos


@dataclass
class Entry:
    path: str        # ruta dentro del disquete, con "/"
    is_dir: bool
    size: int


# ---------------------------------------------------------------- imagen en memoria
class _KeepBuf(io.BytesIO):
    """BytesIO que sobrevive al close() de pyfatfs, para poder leer la imagen final."""
    def close(self):
        pass


class FatImage:
    """Un disquete FAT12 de 1,44 MB en memoria. Mismo código para la vista previa y la copia real."""

    def __init__(self, data: bytes, writable: bool = True, short_names: bool = False):
        if len(data) != SLOT_SIZE or not is_valid_fat12(data):
            raise GotekError("No es una imagen FAT12 de 1,44 MB válida.")
        self.writable = writable
        self.short_names = short_names
        self.dirty = False
        self._alias: dict[tuple[str, str], str] = {}
        self._open(data)

    def _open(self, data: bytes):
        self._buf = _KeepBuf(data)
        try:
            with warnings.catch_warnings():  # «no desmontado limpiamente»: es normal en instantáneas internas
                warnings.simplefilter("ignore")
                self._fs = PyFatBytesIOFS(self._buf)
        except Exception as e:
            raise GotekError(f"No se pudo interpretar el FAT12: {e}") from e

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        try:
            self._fs.close()
        except Exception:
            pass

    def image(self) -> bytes:
        """Imagen completa tal cual (cierra limpiamente el FAT y lo reabre para seguir usándolo)."""
        if self.writable:
            self._fs.close()
        else:
            return self._buf.getvalue()
        data = self._buf.getvalue()
        self._open(data)
        return data

    # -- consulta
    def walk(self, base: str = "/") -> Iterator[Entry]:
        base = self.find(base)
        for info in self._fs.scandir(base):
            p = base.rstrip("/") + "/" + info.name
            if info.is_dir:
                yield Entry(p, True, 0)
                yield from self.walk(p)
            else:
                yield Entry(p, False, info.size)

    def stats(self) -> tuple[int, int]:
        files = used = 0
        for e in self.walk():
            if not e.is_dir:
                files += 1
                used += e.size
        return files, used

    def free_bytes(self) -> int:
        fat = self._fs.fs.fat
        return sum(1 for i in range(2, DATA_CLUSTERS + 2) if fat[i] == 0) * SECTOR

    def find(self, path: str) -> str:
        """Ruta real de `path` ignorando mayúsculas (FAT no las distingue); si no existe, la devuelve tal cual."""
        out = ""
        for part in [p for p in path.split("/") if p]:
            try:
                names = [i.name for i in self._fs.scandir(out or "/")]
            except Exception:
                return path
            match = next((n for n in names if n == part), None) or \
                next((n for n in names if n.lower() == part.lower()), None)
            if match is None:
                return path
            out += "/" + match
        return out or "/"

    def read(self, path: str) -> bytes:
        return self._fs.readbytes(self.find(path))

    def isdir(self, path: str) -> bool:
        return self._fs.isdir(self.find(path))

    def exists(self, path: str) -> bool:
        return self._fs.exists(self.find(path))

    def volume_label(self) -> str:
        return get_label_bytes(self._buf.getvalue())

    # -- nombres
    def _final_name(self, parent: str, name: str) -> str:
        name = clean_name(name)
        if is_short_name(name):
            return name.upper()
        if not self.short_names:
            return name
        key = (parent.lower(), name.lower())
        if key not in self._alias:
            taken = [i.name for i in self._fs.scandir(parent or "/")] if self._fs.isdir(parent or "/") else []
            taken += [v for (p, _), v in self._alias.items() if p == parent.lower()]
            self._alias[key] = to_short_name(name, taken)
        return self._alias[key]

    def resolve_path(self, path: str) -> str:
        """Ruta final que se usará dentro del disquete (mayúsculas 8.3 / alias / nombre largo)."""
        out = ""
        for part in [p for p in path.split("/") if p]:
            out += "/" + self._final_name(out, part)
        return out

    # -- modificación
    def _require_writable(self):
        if not self.writable:
            raise GotekError("Imagen abierta en solo lectura.")

    def makedirs(self, path: str) -> str:
        self._require_writable()
        final = self.resolve_path(path)
        snapshot = self._buf.getvalue()
        try:
            self._fs.makedirs(final, recreate=True)
        except Exception as e:
            self._open(snapshot)
            raise DiskFull(str(e)) from e
        self.dirty = True
        return final

    def write(self, path: str, data: bytes) -> str:
        """Escribe un archivo; devuelve la ruta final. Si no cabe lanza DiskFull y deja la imagen
        exactamente como estaba (pyfatfs puede dejar clusters huérfanos al fallar a medias)."""
        self._require_writable()
        final = self.resolve_path(path)
        parent = final.rsplit("/", 1)[0]
        snapshot = self._buf.getvalue()
        try:
            acc = ""
            for d in [p for p in parent.split("/") if p]:
                acc += "/" + d
                if not self._fs.exists(acc):
                    self._fs.makedir(acc)
            self._fs.writebytes(final, data)
        except Exception as e:  # pyfatfs lanza distintas excepciones al llenarse
            self._open(snapshot)
            raise DiskFull(str(e)) from e
        self.dirty = True
        return final

    def remove(self, path: str):
        self._require_writable()
        path = self.find(path)
        if self._fs.isdir(path):
            self._fs.removetree(path)
        else:
            self._fs.remove(path)
        self.dirty = True

    def clear(self):
        for info in list(self._fs.scandir("/")):
            self.remove("/" + info.name)


class Slot(FatImage):
    """Un slot de un Device. Se trabaja en memoria y se vuelca con commit()
    (o al salir del `with` si hubo cambios)."""

    def __init__(self, dev: Device, n: int, data: Optional[bytes] = None, short_names: bool = False):
        data = data if data is not None else dev.read_slot(n)
        if not is_valid_fat12(data):
            raise GotekError(f"El slot {n} no contiene un FAT12 válido (fórmatealo primero).")
        self.dev, self.n = dev, n
        super().__init__(data, writable=dev.writable, short_names=short_names)

    def __exit__(self, exc_type, *a):
        try:
            if exc_type is None and self.dirty:
                self.commit()
        finally:
            self.close()

    def commit(self):
        self.dev.write_slot(self.n, self.image())
        self.dirty = False


# ---------------------------------------------------------------- utilidades
def extract_tree(slot: FatImage, path: str, dest_dir: str) -> list[str]:
    """Extrae un archivo o una carpeta del disquete a `dest_dir`; devuelve las rutas creadas."""
    path = "/" + path.strip("/")
    name = path.rsplit("/", 1)[-1] or "disquete"
    out: list[str] = []

    def one(src: str, dst: str):
        if slot.isdir(src):
            os.makedirs(dst, exist_ok=True)
            for e in slot._fs.scandir(src):
                one(src.rstrip("/") + "/" + e.name, os.path.join(dst, e.name))
        else:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            with open(dst, "wb") as f:
                f.write(slot.read(src))
            out.append(dst)

    one(path, os.path.join(dest_dir, name))
    return out


def export_image(dev: Device, n: int, out_path: str):
    with open(out_path, "wb") as f:
        f.write(dev.read_slot(n))


def import_image(dev: Device, n: int, in_path: str):
    with open(in_path, "rb") as f:
        data = f.read()
    if len(data) < SLOT_SIZE:
        data = data.ljust(SLOT_SIZE, b"\x00")
    if len(data) != SLOT_SIZE or not is_valid_fat12(data):
        raise GotekError("La imagen no es un disquete FAT12 de 1,44 MB válido.")
    dev.write_slot(n, data)


def backup_device(dev: Device, out_path: str, progress: Optional[Callable[[int, int], None]] = None):
    """Copia todos los slots a un archivo con la misma disposición (STRIDE) que el USB."""
    with open(out_path, "wb") as f:
        for n in range(dev.n_slots):
            f.seek(n * STRIDE)
            f.write(dev.read_slot(n))
            if progress:
                progress(n + 1, dev.n_slots)


def restore_device(dev: Device, in_path: str, progress: Optional[Callable[[int, int], None]] = None):
    """Inverso de backup_device: escribe cada slot de la copia en el dispositivo."""
    size = os.path.getsize(in_path)
    n_src = (size + STRIDE - SLOT_SIZE) // STRIDE if size >= SLOT_SIZE else 0
    if n_src > dev.n_slots:
        raise GotekError(f"La copia tiene {n_src} slots y el destino sólo {dev.n_slots}.")
    with open(in_path, "rb") as f:
        for n in range(n_src):
            f.seek(n * STRIDE)
            dev.write_slot(n, f.read(SLOT_SIZE).ljust(SLOT_SIZE, b"\x00"))
            if progress:
                progress(n + 1, n_src)
