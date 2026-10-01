"""Acceso en crudo a \\\\.\\PhysicalDriveN en Windows (ctypes). SIN PROBAR en Windows real.

- Tamaño con IOCTL_DISK_GET_LENGTH_INFO (el seek al final falla en discos físicos).
- Al escribir se bloquea y desmonta cada volumen del disco (FSCTL_LOCK_VOLUME / FSCTL_DISMOUNT_VOLUME).
- Todas las E/S del núcleo van alineadas a 4 KiB (offsets y longitudes múltiplos de 1536 KiB / 1440 KiB),
  así que valen para sectores de 512 B y de 4 KiB sin lectura-modificación-escritura.
- is_admin() / relaunch_as_admin() para elevar a administrador."""
from __future__ import annotations

import struct
import sys

from .core import GotekError

IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_DISMOUNT_VOLUME = 0x00090020
ALIGN = 4096


def parse_length_info(buf: bytes) -> int:
    """GET_LENGTH_INFORMATION es un LARGE_INTEGER little-endian."""
    return struct.unpack("<q", buf[:8])[0]


def check_aligned(offset: int, length: int):
    if offset % ALIGN or length % ALIGN:
        raise GotekError(f"E/S sin alinear en dispositivo físico (offset={offset}, len={length}).")


def is_admin() -> bool:
    if sys.platform != "win32":
        import os
        return os.geteuid() == 0
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(args: list[str] | None = None) -> bool:
    """Relanza el programa con UAC («runas»). Devuelve True si se lanzó (el proceso actual debería salir)."""
    if sys.platform != "win32":
        return False
    import ctypes
    params = " ".join(f'"{a}"' for a in (args if args is not None else sys.argv))
    exe = sys.executable
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32


class WinRawFile:
    """Archivo-like (seek/read/write/fileno/close) sobre \\\\.\\PhysicalDriveN."""

    def __init__(self, path: str, writable: bool):
        if sys.platform != "win32":
            raise GotekError("Los dispositivos \\\\.\\PhysicalDriveN sólo existen en Windows.")
        import ctypes
        import msvcrt
        from ctypes import wintypes
        self._ctypes, self._wintypes = ctypes, wintypes
        self._locks: list = []
        self.writable = writable
        if writable:
            self._lock_volumes(path)
        self._f = open(path, "r+b" if writable else "rb", buffering=0)
        handle = msvcrt.get_osfhandle(self._f.fileno())
        buf = ctypes.create_string_buffer(8)
        ret = wintypes.DWORD()
        ok = ctypes.windll.kernel32.DeviceIoControl(wintypes.HANDLE(handle), IOCTL_DISK_GET_LENGTH_INFO,
                                                    None, 0, buf, 8, ctypes.byref(ret), None)
        if not ok:
            self._f.close()
            raise GotekError(f"No se pudo obtener el tamaño de {path} (error {ctypes.GetLastError()}).")
        self.size = parse_length_info(buf.raw)
        self._pos = 0

    def _lock_volumes(self, path: str):
        from .devices import _ps, windows_disk_number
        ct, wt = self._ctypes, self._wintypes
        n = windows_disk_number(path)
        try:
            letters = _ps(f"(Get-Partition -DiskNumber {n} -ErrorAction SilentlyContinue).DriveLetter").split()
        except Exception:
            letters = []
        k32 = ct.windll.kernel32
        k32.CreateFileW.restype = wt.HANDLE
        for L in (x for x in letters if x.strip()):
            h = k32.CreateFileW(f"\\\\.\\{L}:", 0xC0000000, 3, None, 3, 0, None)
            if h in (None, wt.HANDLE(-1).value, -1):
                raise GotekError(f"No se pudo abrir la unidad {L}: para bloquearla (¿está en uso?).")
            ret = wt.DWORD()
            if not k32.DeviceIoControl(h, FSCTL_LOCK_VOLUME, None, 0, None, 0, ct.byref(ret), None):
                k32.CloseHandle(h)
                raise GotekError(f"La unidad {L}: está en uso; ciérrala (Explorador, antivirus…) y reintenta.")
            k32.DeviceIoControl(h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ct.byref(ret), None)
            self._locks.append(h)

    def seek(self, pos: int, whence: int = 0) -> int:
        if whence == 2:
            pos = self.size + pos
        elif whence == 1:
            pos = self._pos + pos
        self._pos = pos
        return pos

    def read(self, n: int) -> bytes:
        check_aligned(self._pos, n)
        self._f.seek(self._pos)
        data = self._f.read(n)
        self._pos += len(data)
        return data

    def write(self, data: bytes) -> int:
        check_aligned(self._pos, len(data))
        self._f.seek(self._pos)
        w = self._f.write(data)
        self._pos += len(data)
        return w

    def fileno(self) -> int:
        return self._f.fileno()

    def close(self):
        try:
            self._f.close()
        finally:
            for h in self._locks:
                self._ctypes.windll.kernel32.CloseHandle(h)
            self._locks = []
