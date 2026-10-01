"""Genera binarios con PyInstaller: `python build_pyinstaller.py` (en Windows -> .exe; en Linux -> binario).
Hay que ejecutarlo en cada sistema operativo (PyInstaller no compila de forma cruzada)."""
import subprocess
import sys

COMMON = ["--noconfirm", "--clean", "--collect-all", "pyfatfs", "--collect-all", "fs", "--collect-all", "sv_ttk", "--collect-all", "tkinterdnd2"]
try:
    import tkinterdnd2  # noqa: F401
    COMMON += ["--collect-all", "tkinterdnd2"]
except ImportError:
    pass

TARGETS = [
    ("usbfloppy", "usbfloppy.py", ["--console"]),
    ("usbfloppy-gui", "usbfloppy_gui.py", ["--windowed"]),
]
# En Windows el .exe pide elevación (UAC) para poder abrir \\.\PhysicalDriveN
if sys.platform == "win32":
    for _, _, extra in TARGETS:
        extra += ["--uac-admin"]

for name, script, extra in TARGETS:
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--name", name, *COMMON, *extra, script]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
print("Listo: ver carpeta dist/")
