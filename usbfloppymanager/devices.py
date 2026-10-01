"""Detección de dispositivos USB extraíbles y comprobaciones de seguridad (Linux y Windows).

La parte de Windows (PowerShell, \\\\.\\PhysicalDriveN) NO se ha podido probar en Windows real;
la lógica pura (parseo, decisiones) sí tiene tests. Ver README, «Pendiente de validar»."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from .core import GotekError

SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/usr", "/var", "/home", "/etc", "/opt", "/nix", "/efi")
_IGNORED_PREFIX = ("loop", "ram", "zram", "dm-", "md", "sr", "nbd", "fd")


@dataclass
class UsbDevice:
    path: str
    size: int
    model: str
    removable: bool = True
    system: bool = False       # contiene el sistema operativo: nunca se escribe
    mounted: List[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return f"{self.path}  {self.model or '(sin modelo)'}  {human_size(self.size)}"


def human_size(n: float) -> str:
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or u == "TiB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return str(n)


# ---------------------------------------------------------------- rutas de Windows
_RE_WINRAW = re.compile(r"^\\\\\.\\PHYSICALDRIVE(\d+)$", re.I)


def is_windows_raw_path(path: str) -> bool:
    return bool(_RE_WINRAW.match(path))


def windows_disk_number(path: str) -> Optional[int]:
    m = _RE_WINRAW.match(path)
    return int(m.group(1)) if m else None


def open_windows_raw(path: str, writable: bool):
    from .winraw import WinRawFile
    return WinRawFile(path, writable)


def permission_hint(path: str) -> str:
    if sys.platform == "win32":
        return (f"Sin permisos para abrir {path}. Ejecuta el programa como administrador "
                "(clic derecho → «Ejecutar como administrador») y cierra los programas que usen la unidad.")
    return (f"Sin permisos para abrir {path}. Ejecuta con sudo, o añade tu usuario al grupo «disk» "
            "(sudo usermod -aG disk $USER y vuelve a iniciar sesión).")


# ---------------------------------------------------------------- Linux
def parse_mounts(text: str) -> List[tuple[str, str]]:
    """[(dispositivo, punto de montaje)] de /proc/mounts (desescapa \\040)."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1].replace("\\040", " ")))
    return out


def _linux_disk_name(devname: str, sys_class: str = "/sys/class/block") -> str:
    """sda1 -> sda; nvme0n1p2 -> nvme0n1; sda -> sda (usa sysfs)."""
    p = os.path.realpath(os.path.join(sys_class, devname))
    if os.path.exists(os.path.join(p, "partition")):
        return os.path.basename(os.path.dirname(p))
    return devname


def _linux_info(name: str, sys_block: str = "/sys/block"):
    base = os.path.join(sys_block, name)

    def rd(f):
        with open(os.path.join(base, f)) as fh:
            return fh.read().strip()
    removable = False
    try:
        removable = rd("removable") == "1"
    except OSError:
        pass
    usb = "/usb" in os.path.realpath(base)
    try:
        model = rd("device/model")
    except OSError:
        model = ""
    try:
        size = int(rd("size")) * 512
    except (OSError, ValueError):
        size = 0
    return removable or usb, size, model


def linux_mounts_for_disk(disk: str, mounts_text: Optional[str] = None) -> List[str]:
    if mounts_text is None:
        try:
            with open("/proc/self/mounts") as f:
                mounts_text = f.read()
        except OSError:
            return []
    points = []
    for dev, mp in parse_mounts(mounts_text):
        if dev.startswith("/dev/"):
            real = os.path.realpath(dev)
            try:
                if _linux_disk_name(os.path.basename(real)) == disk:
                    points.append(mp)
            except OSError:
                continue
    return points


def list_usb_devices(include_fixed: bool = False) -> List[UsbDevice]:
    """USB extraíbles detectados. `include_fixed=True` añade el resto de discos (marcando el del sistema)."""
    if sys.platform.startswith("linux"):
        out = []
        base = "/sys/block"
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return []
        for name in names:
            if name.startswith(_IGNORED_PREFIX):
                continue
            try:
                removable, size, model = _linux_info(name)
            except OSError:
                continue
            if size == 0 or (not removable and not include_fixed):
                continue
            mounts = linux_mounts_for_disk(name)
            system = any(m in SYSTEM_MOUNTS for m in mounts)
            out.append(UsbDevice(f"/dev/{name}", size, model, removable, system, mounts))
        return out
    if sys.platform == "win32":
        return _list_windows(include_fixed)
    return []


# ---------------------------------------------------------------- Windows
def _ps(cmd: str, timeout: int = 20) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=timeout).stdout.strip()


def parse_windows_disks(raw: str, system_disk: Optional[int] = None,
                        include_fixed: bool = False) -> List[UsbDevice]:
    """Interpreta el JSON de Win32_DiskDrive (InterfaceType, MediaType, DeviceID, Size, Model, Index)."""
    if not raw:
        return []
    data = json.loads(raw)
    data = data if isinstance(data, list) else [data]
    out = []
    for d in data:
        removable = (d.get("InterfaceType") == "USB") or str(d.get("MediaType") or "").startswith("Removable")
        if not removable and not include_fixed:
            continue
        idx = d.get("Index")
        system = system_disk is not None and idx == system_disk
        out.append(UsbDevice(d["DeviceID"], int(d.get("Size") or 0), d.get("Model") or "", removable, system))
    return out


def _windows_system_disk() -> Optional[int]:
    try:
        raw = _ps("(Get-Partition -DriveLetter ($env:SystemDrive.Substring(0,1))).DiskNumber")
        return int(raw)
    except Exception:
        return None


def _list_windows(include_fixed: bool) -> List[UsbDevice]:
    try:
        raw = _ps("Get-CimInstance Win32_DiskDrive | Select-Object DeviceID,Index,Size,Model,"
                  "InterfaceType,MediaType | ConvertTo-Json")
        return parse_windows_disks(raw, _windows_system_disk(), include_fixed)
    except Exception:
        return []


# ---------------------------------------------------------------- seguridad
@dataclass
class TargetInfo:
    path: str
    is_partition: bool = False
    removable: bool = True
    system: bool = False
    mounted: List[str] = field(default_factory=list)


def evaluate_target(t: TargetInfo, force: bool) -> None:
    """Decide si se puede escribir en el dispositivo. Lanza GotekError con un mensaje claro."""
    if t.system:
        raise GotekError(f"{t.path} contiene el sistema operativo: se rechaza SIEMPRE, ni con --force.")
    if t.is_partition and not force:
        raise GotekError(f"{t.path} es una partición. Los disquetes del Gotek ocupan el disco entero "
                         "(p. ej. /dev/sdb, no /dev/sdb1). Usa --force sólo si sabes lo que haces.")
    if not t.removable and not force:
        raise GotekError(f"{t.path} no es un dispositivo extraíble; se rechaza por seguridad. "
                         "Si de verdad quieres usarlo, repite con --force.")
    if t.mounted and not force:
        raise GotekError(f"{t.path} tiene volúmenes montados ({', '.join(t.mounted)}). Desmóntalo antes, "
                         "o usa --force bajo tu responsabilidad.")


def _linux_target(path: str) -> TargetInfo:
    real = os.path.realpath(path)
    name = os.path.basename(real)
    disk = _linux_disk_name(name)
    removable, _, _ = _linux_info(disk)
    mounts = linux_mounts_for_disk(disk)
    return TargetInfo(path, is_partition=disk != name, removable=removable,
                      system=any(m in SYSTEM_MOUNTS for m in mounts), mounted=mounts)


def _windows_target(path: str) -> TargetInfo:
    n = windows_disk_number(path)
    devs = _list_windows(include_fixed=True)
    for d in devs:
        if windows_disk_number(d.path) == n:
            return TargetInfo(path, removable=d.removable, system=d.system)
    return TargetInfo(path, removable=False)


def check_writable_target(path: str, force: bool = False) -> None:
    """Los archivos de imagen siempre se pueden escribir; los dispositivos en crudo pasan por evaluate_target."""
    if is_windows_raw_path(path):
        evaluate_target(_windows_target(path), force)
        return
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return  # Device lanzará el error de apertura adecuado
    if stat.S_ISBLK(mode) and sys.platform.startswith("linux"):
        evaluate_target(_linux_target(path), force)
    elif not stat.S_ISREG(mode):
        raise GotekError(f"{path} no es un archivo de imagen ni un dispositivo de bloques.")
