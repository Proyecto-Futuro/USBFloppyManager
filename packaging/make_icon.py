"""Genera un icono PNG 256x256 (un disquete sencillo) sin dependencias."""
import struct
import sys
import zlib

N = 256


def pixel(x, y):
    if 24 <= x < 232 and 24 <= y < 232:
        if 70 <= x < 186 and 24 <= y < 96:       # persiana metálica
            return (200, 200, 205, 255)
        if 56 <= x < 200 and 140 <= y < 232:      # etiqueta
            return (240, 240, 235, 255)
        return (40, 70, 140, 255)
    return (0, 0, 0, 0)


raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(N)) for y in range(N))


def chunk(t, d):
    c = struct.pack(">I", len(d)) + t + d
    return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)


png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0)) + \
    chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
open(sys.argv[1] if len(sys.argv) > 1 else "usbfloppymanager.png", "wb").write(png)
