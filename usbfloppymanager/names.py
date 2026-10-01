"""Nombres de archivo FAT: validación 8.3 y generación de alias únicos (NOMBRE~1.EXT)."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_SFN_CHARS = r"A-Za-z0-9!#$%&'()\-@^_`{}~"
_RE_SFN = re.compile(rf"^[{_SFN_CHARS}]{{1,8}}(\.[{_SFN_CHARS}]{{1,3}})?$")
_ILLEGAL = '"*/:<>?\\|'


def is_short_name(name: str) -> bool:
    """¿Es un nombre 8.3 válido (sin espacios, sin acentos)? Las minúsculas cuentan como válidas."""
    return bool(_RE_SFN.match(name)) and name not in (".", "..")


def clean_name(name: str) -> str:
    """Quita lo que FAT no admite ni siquiera como nombre largo."""
    name = "".join("_" if (c in _ILLEGAL or ord(c) < 32) else c for c in name)
    name = name.rstrip(" .")
    if not name:
        name = "_"
    return name[:255]


def _ascii_part(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    out = []
    for c in s.upper():
        if re.match(rf"[{_SFN_CHARS}]", c):
            out.append(c)
        elif c in " .":
            continue
        else:
            out.append("_")
    return "".join(out)


def to_short_name(name: str, taken: Iterable[str] = ()) -> str:
    """Convierte `name` en un 8.3 en mayúsculas distinto de los de `taken` (sin distinguir mayúsculas)."""
    taken_u = {t.upper() for t in taken}
    if is_short_name(name) and name.upper() not in taken_u:
        return name.upper()
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem, ext = _ascii_part(stem) or "_", _ascii_part(ext)[:3]
    suffix = f".{ext}" if ext else ""
    i = 1
    while True:
        tail = f"~{i}"
        cand = stem[:8 - len(tail)] + tail + suffix
        if cand not in taken_u:
            return cand
        i += 1
