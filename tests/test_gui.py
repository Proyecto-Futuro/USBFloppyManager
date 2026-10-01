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
