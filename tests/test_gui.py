import shutil
import subprocess
import sys

import pytest

PY = next((p for p in (sys.executable, "/usr/bin/python3.12", "/usr/bin/python3") if p and shutil.which(p)), None)


def _tk_python():
    for p in (sys.executable, "/usr/bin/python3.12", "/usr/bin/python3"):
        if p and subprocess.run([p, "-c", "import tkinter"], capture_output=True).returncode == 0:
            return p
    return None


@pytest.mark.skipif(_tk_python() is None or shutil.which("xvfb-run") is None, reason="sin tkinter/xvfb")
def test_gui_starts_and_lists_slots(formatted):
    code = f'''
import sys, time
sys.path[:0] = {[p for p in sys.path if "packages" in p] + ["."]!r}
import tkinter as tk
from tkinter import messagebox
messagebox.showinfo = messagebox.showerror = lambda *a, **k: print("MSGBOX", a)
from usbfloppymanager.gui import App
root = tk.Tk(); a = App(root)
def pump(t):
    e = time.time() + t
    while time.time() < e: root.update(); time.sleep(0.02)
pump(1)
a.images.append({formatted!r}); a.reload_devices(); pump(1)
a.dev_combo.set("Imagen: " + {formatted!r}); a.refresh_slots(); pump(2)
rows = [a.slots.item(i, "values") for i in a.slots.get_children()]
assert len(rows) == 8 and rows[0][4] == "sí", rows
print("OK")
'''
    r = subprocess.run(["xvfb-run", "-a", _tk_python(), "-c", code], capture_output=True, text=True,
                       timeout=90, cwd=".")
    assert "OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(_tk_python() is None or shutil.which("xvfb-run") is None, reason="sin tkinter/xvfb")
def test_gui_add_folder_updates_only_that_slot(formatted, tmp_path):
    """Regresión: tras copiar no se relee todo el USB, el explorador se actualiza y se copian carpetas vacías."""
    src = tmp_path / "src"
    (src / "Datos").mkdir(parents=True)
    (src / "Datos" / "Archivo largo.txt").write_text("x")
    (src / "Vacia").mkdir()
    (src / "Mis fotos" / "x").mkdir(parents=True)
    code = f'''
import sys, time
sys.path[:0] = {[p for p in sys.path if "packages" in p] + ["."]!r}
import tkinter as tk
from tkinter import messagebox
messagebox.showinfo = messagebox.showerror = lambda *a, **k: print("MSGBOX", a)
messagebox.askyesno = lambda *a, **k: True
from usbfloppymanager.gui import App
root = tk.Tk(); a = App(root)
def pump(t):
    e = time.time() + t
    while time.time() < e: root.update(); time.sleep(0.02)
pump(1)
a.images.append({formatted!r}); a.reload_devices(); pump(1)
a.dev_combo.set("Imagen: " + {formatted!r}); a.refresh_slots(); pump(2)
a.slots.selection_set("1"); pump(1.5)
a.short_var.set(True)
for f in ("Datos", "Vacia", "Mis fotos"):
    a.add_paths([{str(src)!r} + "/" + f]); pump(1.5)
def allitems(parent=""):
    for i in a.files.get_children(parent):
        yield i
        yield from allitems(i)
names = sorted(allitems())
assert names == ["/DATOS", "/DATOS/ARCHIV~1.TXT", "/MISFOT~1", "/MISFOT~1/X", "/VACIA"], names
log = a.logbox.get("1.0", "end")
assert log.count("slots") == 2, log   # sólo las 2 lecturas del arranque: ninguna relectura completa al copiar
print("OK")
'''
    r = subprocess.run(["xvfb-run", "-a", _tk_python(), "-c", code], capture_output=True, text=True,
                       timeout=90, cwd=".")
    assert "OK" in r.stdout, r.stdout + r.stderr
