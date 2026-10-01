"""Interfaz gráfica (tkinter). Sólo llama a core/batch/devices; no contiene lógica de disquetes.
Las operaciones largas corren en un hilo y devuelven el resultado por una cola (la UI no se congela)."""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Optional

from . import __version__
from .batch import MODES, run_batch, write_contents
from .core import (DATA_CLUSTERS, SECTOR, Device, GotekError, Slot, backup_device, export_image,
                   extract_tree, import_image, restore_device)
from .devices import UsbDevice, human_size, list_usb_devices

try:  # tema moderno opcional (aspecto Windows 11, claro/oscuro)
    import sv_ttk
except Exception:  # pragma: no cover
    sv_ttk = None

try:  # arrastrar y soltar es opcional
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # pragma: no cover
    DND_FILES = TkinterDnD = None


def system_prefers_dark() -> bool:
    """Mejor esfuerzo para detectar el modo oscuro del sistema (Windows / GNOME / macOS)."""
    import subprocess
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            return subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                  capture_output=True, text=True, timeout=2).stdout.strip() == "Dark"
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                             capture_output=True, text=True, timeout=2).stdout
        return "dark" in out
    except Exception:  # noqa: BLE001
        return False


def usage_bar(frac: float, width: int = 12) -> str:
    n = max(0, min(width, round(frac * width)))
    return "━" * n + "─" * (width - n)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"USBFloppyManager {__version__}")
        root.geometry("1150x760")
        root.minsize(900, 600)
        self.q: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.devices: list[UsbDevice] = []
        self.images: list[str] = []
        self.dev_label_to_path: dict[str, str] = {}
        self.busy = False
        self._ctx_path, self._ctx_force, self._ctx_slots = None, False, None
        self.cancel_flag = threading.Event()
        self.current_slot: Optional[int] = None
        self.force_var = tk.BooleanVar(value=False)
        self.fixed_var = tk.BooleanVar(value=False)
        self.short_var = tk.BooleanVar(value=False)
        self.slots_var = tk.IntVar(value=100)
        self.modern = sv_ttk is not None
        self.accent = "Accent.TButton" if self.modern else "TButton"
        if self.modern:
            sv_ttk.set_theme("dark" if system_prefers_dark() else "light")
        style = ttk.Style()
        style.configure("Treeview", rowheight=26)
        style.configure("Title.TLabel", font=("TkDefaultFont", 11, "bold"))
        self._build()
        self._poll()
        self.reload_devices()

    # ------------------------------------------------------------ hilos
    def _poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def run_async(self, work: Callable[[], object], done: Optional[Callable[[object], None]] = None,
                  what: str = "Trabajando…"):
        if self.busy:
            messagebox.showinfo("Ocupado", "Hay otra operación en curso.")
            return
        self.busy = True
        # snapshot en el hilo de la UI: los hilos de trabajo no deben tocar widgets tk
        self._ctx_path, self._ctx_force = self.path, self.force_var.get()
        self._ctx_slots = self._slots_limit()
        self.status(what)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        def runner():
            try:
                res, err = work(), None
            except GotekError as e:
                res, err = None, str(e)
            except Exception as e:  # noqa: BLE001
                res, err = None, f"{type(e).__name__}: {e}"
            self.q.put(lambda: self._finish(res, err, done))
        threading.Thread(target=runner, daemon=True).start()

    def _finish(self, res, err, done):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        if err:
            self.status("Error")
            self.log(f"ERROR: {err}")
            messagebox.showerror("Error", err)
        else:
            self.status("Listo")
            if done:
                done(res)

    def set_progress(self, i: int, n: int):
        def upd():
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(n, 1), value=i)
        self.q.put(upd)

    # ------------------------------------------------------------ UI
    def _build(self):
        top = ttk.Frame(self.root, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        row1 = ttk.Frame(top)
        row1.pack(fill="x")
        ttk.Label(row1, text="Dispositivo", style="Title.TLabel").pack(side="left")
        self.dev_combo = ttk.Combobox(row1, state="readonly")
        self.dev_combo.pack(side="left", padx=8, fill="x", expand=True)
        self.dev_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_slots())
        ttk.Button(row1, text="⟳ Recargar", command=self.reload_devices).pack(side="left")
        ttk.Button(row1, text="Abrir imagen…", command=self.open_image).pack(side="left", padx=6)
        if self.modern:
            ttk.Button(row1, text="☾ / ☀", width=6, command=self.toggle_theme).pack(side="left")
        if sys.platform == "win32":
            from .winraw import is_admin, relaunch_as_admin
            if not is_admin():
                ttk.Button(row1, text="Reiniciar como administrador",
                           command=lambda: relaunch_as_admin() and self.root.destroy()).pack(side="left", padx=6)
        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="Nº slots (0 = todos)").pack(side="left")
        ttk.Spinbox(row2, from_=0, to=9999, width=5, textvariable=self.slots_var).pack(side="left", padx=6)
        ttk.Button(row2, text="Aplicar", command=self.refresh_slots).pack(side="left")
        ttk.Checkbutton(row2, text="Nombres 8.3", variable=self.short_var).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(row2, text="Mostrar no extraíbles", variable=self.fixed_var,
                        command=self.reload_devices).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(row2, text="Forzar (peligroso)", variable=self.force_var).pack(side="left", padx=(18, 0))

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)
        self._build_slots_tab()
        self._build_batch_tab()

        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 4))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, length=260)
        self.progress.pack(side="right")
        self.status_var = tk.StringVar(value="Listo")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")
        self.logbox = tk.Text(self.root, height=6, state="disabled", relief="flat", borderwidth=8,
                              font=("TkFixedFont", 9))
        self.logbox.pack(fill="x", padx=12, pady=(0, 12))

    def _build_slots_tab(self):
        f = ttk.Frame(self.nb, padding=4)
        self.nb.add(f, text="Disquetes")
        paned = ttk.PanedWindow(f, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        cols = ("n", "label", "files", "bar", "valid")
        self.slots = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended")
        for c, t, w in (("n", "Nº", 40), ("label", "Etiqueta", 90), ("files", "Archivos", 65),
                        ("bar", "Uso", 190), ("valid", "Válido", 55)):
            self.slots.heading(c, text=t)
            self.slots.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(left, command=self.slots.yview)
        self.slots.configure(yscrollcommand=sb.set)
        self.slots.pack(side="top", fill="both", expand=True)
        self.slots.bind("<<TreeviewSelect>>", lambda e: self.on_slot_select())
        for group in ((("Formatear", self.act_format), ("Formatear todos", self.act_format_all),
                       ("Etiqueta…", self.act_label)),
                      (("Importar .img…", self.act_import), ("Exportar .img…", self.act_export),
                       ("Copia de seguridad…", self.act_backup), ("Restaurar…", self.act_restore))):
            row = ttk.Frame(left)
            row.pack(fill="x", pady=2)
            for text, cmd in group:
                ttk.Button(row, text=text, command=cmd).pack(side="left", padx=2)

        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        self.explorer_title = tk.StringVar(value="Selecciona un disquete")
        ttk.Label(right, textvariable=self.explorer_title, style="Title.TLabel").pack(anchor="w", pady=(0, 4))
        self.files = ttk.Treeview(right, columns=("size",), selectmode="extended")
        self.files.heading("#0", text="Nombre")
        self.files.heading("size", text="Tamaño")
        self.files.column("size", width=90, anchor="e")
        self.files.pack(fill="both", expand=True)
        for group in ((("Añadir…", self.act_add), ("Añadir carpeta…", self.act_add_dir),
                       ("Nueva carpeta…", self.act_mkdir)),
                      (("Extraer…", self.act_extract), ("Borrar", self.act_delete))):
            row = ttk.Frame(right)
            row.pack(fill="x", pady=2)
            for text, cmd in group:
                ttk.Button(row, text=text, command=cmd,
                           style=self.accent if text == "Añadir…" else "TButton").pack(side="left", padx=2)
        if TkinterDnD is not None:
            try:
                self.files.drop_target_register(DND_FILES)
                self.files.dnd_bind("<<Drop>>", self.on_drop)
                ttk.Label(right, text="Puedes arrastrar archivos aquí.").pack(anchor="w")
            except Exception:  # noqa: BLE001
                pass
        else:
            ttk.Label(right, text="(Instala tkinterdnd2 para arrastrar y soltar; mientras, usa «Añadir».)"
                      ).pack(anchor="w")

    def _build_batch_tab(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="Copia por lotes")
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Label(r, text="Carpeta origen:").pack(side="left")
        self.src_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.src_var, width=60).pack(side="left", padx=4)
        ttk.Button(r, text="Examinar…", command=lambda: self.src_var.set(
            filedialog.askdirectory() or self.src_var.get())).pack(side="left")
        ttk.Label(r, text="  Slot inicial:").pack(side="left")
        self.start_var = tk.IntVar(value=0)
        ttk.Spinbox(r, from_=0, to=9999, width=6, textvariable=self.start_var).pack(side="left")
        self.mode_var = tk.IntVar(value=3)
        mf = ttk.LabelFrame(f, text="Modo", padding=4)
        mf.pack(fill="x", pady=6)
        for k, v in MODES.items():
            ttk.Radiobutton(mf, text=f"{k}. {v}", value=k, variable=self.mode_var).pack(anchor="w")
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Button(r, text="Vista previa", command=self.act_preview).pack(side="left")
        ttk.Button(r, text="Ejecutar copia", command=self.act_batch, style=self.accent).pack(side="left", padx=6)
        ttk.Button(r, text="Cancelar", command=self.cancel_flag.set).pack(side="left")
        self.batch_tree = ttk.Treeview(f, columns=("size", "note"), selectmode="browse")
        self.batch_tree.heading("#0", text="Disquete / archivo")
        self.batch_tree.heading("size", text="Tamaño")
        self.batch_tree.heading("note", text="Nota")
        self.batch_tree.column("size", width=90, anchor="e")
        self.batch_tree.pack(fill="both", expand=True, pady=6)

    def toggle_theme(self):
        if sv_ttk is not None:
            sv_ttk.toggle_theme()

    # ------------------------------------------------------------ utilidades UI
    def status(self, s: str):
        self.status_var.set(s)

    def log(self, s: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", s + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    @property
    def path(self) -> Optional[str]:
        return self.dev_label_to_path.get(self.dev_combo.get())

    def dev_info(self) -> Optional[UsbDevice]:
        return next((d for d in self.devices if d.path == self.path), None)

    def describe_target(self) -> str:
        d = self.dev_info()
        if d:
            size = human_size(d.size)
            return f"Dispositivo: {d.path}\nModelo: {d.model or '(desconocido)'}\nTamaño: {size}"
        p = self.path
        sz = human_size(os.path.getsize(p)) if p and os.path.isfile(p) else "?"
        return f"Archivo de imagen: {p}\nTamaño: {sz}"

    def confirm(self, what: str) -> bool:
        return messagebox.askyesno("Confirmar operación destructiva",
                                   f"{what}\n\n{self.describe_target()}\n\nEsta operación NO se puede deshacer. ¿Continuar?",
                                   icon="warning", default="no")

    def open_dev(self, writable=False) -> Device:
        if not self._ctx_path:
            raise GotekError("Selecciona primero un dispositivo o abre una imagen.")
        return Device(self._ctx_path, writable=writable, force=self._ctx_force, slots=self._ctx_slots)

    def _slots_limit(self):
        try:
            n = int(self.slots_var.get())
        except (tk.TclError, ValueError):
            return None
        return n if n > 0 else None  # 0 = todos los que quepan

    def selected_slots(self) -> list[int]:
        return [int(self.slots.item(i, "values")[0]) for i in self.slots.selection()]

    def require_slot(self) -> Optional[int]:
        s = self.selected_slots()
        if not s:
            messagebox.showinfo("Selecciona un disquete", "Elige un disquete de la tabla.")
            return None
        return s[0]

    # ------------------------------------------------------------ dispositivos
    def reload_devices(self):
        fixed = self.fixed_var.get()

        def work():
            return list_usb_devices(include_fixed=fixed)

        def done(devs):
            self.devices = devs
            self.dev_label_to_path = {}
            for d in devs:
                tag = "  [SISTEMA]" if d.system else ""
                self.dev_label_to_path[d.description + tag] = d.path
            for p in self.images:
                self.dev_label_to_path[f"Imagen: {p}"] = p
            labels = list(self.dev_label_to_path)
            self.dev_combo.configure(values=labels)
            if labels and self.dev_combo.get() not in labels:
                self.dev_combo.set(labels[0])
            elif not labels:
                self.dev_combo.set("")
            self.refresh_slots()
        self.run_async(work, done, "Buscando dispositivos…")

    def open_image(self):
        p = filedialog.askopenfilename(title="Abrir imagen de USB/disquetes",
                                       filetypes=[("Imágenes", "*.img *.bin *.ima"), ("Todos", "*")])
        if p:
            self.images.append(p)
            self.reload_devices()
            self.dev_combo.set(f"Imagen: {p}")
            self.refresh_slots()

    def refresh_slots(self, keep: Optional[list[int]] = None):
        self.slots.delete(*self.slots.get_children())
        self.files.delete(*self.files.get_children())
        self.current_slot = None
        if not self.path:
            return

        def work():
            with Device(self._ctx_path, slots=self._ctx_slots) as d:
                infos = []
                for i in d.iter_info():
                    infos.append(i)
                    self.set_progress(i.index + 1, d.n_slots)
                return infos

        def done(infos):
            for i in infos:
                frac = i.used_bytes / i.total_bytes if i.total_bytes else 0
                self.slots.insert("", "end", iid=str(i.index), values=(
                    i.index, i.label if i.valid else "", i.files if i.valid else "",
                    f"{usage_bar(frac)} {frac * 100:3.0f}%" if i.valid else "", "sí" if i.valid else "no"))
            for n in keep or []:
                if self.slots.exists(str(n)):
                    self.slots.selection_add(str(n))
            self.log(f"{self.path}: {len(infos)} slots")
        self.run_async(work, done, "Leyendo slots…")

    # ------------------------------------------------------------ explorador
    def on_slot_select(self):
        s = self.selected_slots()
        if len(s) == 1 and s[0] != self.current_slot:
            self.load_slot(s[0])

    def load_slot(self, n: int):
        self.current_slot = n
        self.files.delete(*self.files.get_children())
        self.explorer_title.set(f"Disquete {n}")

        def work():
            with Device(self._ctx_path, slots=self._ctx_slots) as d, Slot(d, n) as sl:
                return list(sl.walk())

        def done(entries):
            for e in entries:
                parent, _, name = e.path.rpartition("/")
                self.files.insert(parent or "", "end", iid=e.path, text=name, open=True,
                                  values=("" if e.is_dir else human_size(e.size),))
        try:
            self.run_async(work, done, f"Leyendo disquete {n}…")
        except GotekError:
            pass

    def target_dir(self) -> str:
        sel = self.files.selection()
        if not sel:
            return ""
        p = sel[0]
        return p if self.files.item(p, "values")[0] == "" else p.rpartition("/")[0]

    def _after_edit(self, msg: str):
        def done(_):
            self.log(msg)
            keep = self.selected_slots()
            n = self.current_slot
            self.refresh_slots(keep)
            if n is not None:
                self.root.after(200, lambda: self.load_slot(n))
        return done

    def add_paths(self, paths: list[str]):
        n, dest, short = self.current_slot, self.target_dir(), self.short_var.get()
        if n is None:
            messagebox.showinfo("Selecciona un disquete", "Elige primero un disquete.")
            return
        if not self.confirm(f"Se añadirán {len(paths)} elemento(s) al disquete {n}."):
            return

        def work():
            count = 0
            with self.open_dev(True) as d, Slot(d, n, short_names=short) as sl:
                for p in paths:
                    base = os.path.dirname(p.rstrip(os.sep))
                    targets = [p] if os.path.isfile(p) else [
                        os.path.join(r, f) for r, _, fs in os.walk(p) for f in fs]
                    for t in targets:
                        rel = os.path.relpath(t, base).replace(os.sep, "/")
                        with open(t, "rb") as fh:
                            sl.write("/" + (dest.strip("/") + "/" if dest else "") + rel, fh.read())
                        count += 1
            return count
        self.run_async(work, lambda c: self._after_edit(f"{c} archivo(s) añadidos al disquete {n}")(None),
                       "Copiando…")

    def act_add(self):
        paths = filedialog.askopenfilenames(title="Añadir archivos")
        if paths:
            self.add_paths(list(paths))

    def act_add_dir(self):
        p = filedialog.askdirectory(title="Añadir carpeta")
        if p:
            self.add_paths([p])

    def on_drop(self, event):
        self.add_paths(list(self.root.tk.splitlist(event.data)))

    def act_extract(self):
        sel, n = self.files.selection(), self.current_slot
        if not sel or n is None:
            return
        out = filedialog.askdirectory(title="Carpeta de destino")
        if not out:
            return

        def work():
            with self.open_dev() as d, Slot(d, n) as sl:
                return sum(len(extract_tree(sl, p, out)) for p in sel)
        self.run_async(work, lambda c: self.log(f"{c} archivo(s) extraídos a {out}"), "Extrayendo…")

    def act_delete(self):
        sel, n = self.files.selection(), self.current_slot
        if not sel or n is None or not self.confirm(f"Se borrarán {len(sel)} elemento(s) del disquete {n}."):
            return

        def work():
            with self.open_dev(True) as d, Slot(d, n) as sl:
                for p in sel:
                    sl.remove(p)
        self.run_async(work, self._after_edit(f"Borrado en el disquete {n}"), "Borrando…")

    def act_mkdir(self):
        n = self.current_slot
        name = simpledialog.askstring("Nueva carpeta", "Nombre:") if n is not None else None
        if not name:
            return
        base, short = self.target_dir(), self.short_var.get()

        def work():
            with self.open_dev(True) as d, Slot(d, n, short_names=short) as sl:
                sl.makedirs(f"{base}/{name}")
        self.run_async(work, self._after_edit(f"Carpeta creada en el disquete {n}"), "Creando carpeta…")

    # ------------------------------------------------------------ acciones sobre slots
    def act_format(self):
        sl = self.selected_slots()
        if not sl or not self.confirm(f"Se FORMATEARÁN los disquetes {', '.join(map(str, sl))}."):
            return

        def work():
            with self.open_dev(True) as d:
                for n in sl:
                    d.format_slot(n, f"FD{n:03d}")
        self.run_async(work, self._after_edit(f"Formateados: {sl}"), "Formateando…")

    def act_format_all(self):
        n_all = len(self.slots.get_children())
        if not n_all or not self.confirm(f"Se FORMATEARÁN TODOS los disquetes mostrados (0 a {n_all - 1})."):
            return

        def work():
            with self.open_dev(True) as d:
                for n in range(d.n_slots):
                    d.format_slot(n, f"FD{n:03d}")
                    self.set_progress(n + 1, d.n_slots)
        self.run_async(work, self._after_edit(f"Formateados los {n_all} disquetes"), "Formateando…")

    def act_label(self):
        n = self.require_slot()
        label = simpledialog.askstring("Etiqueta", "Nueva etiqueta (máx. 11 caracteres):") if n is not None else None
        if not label:
            return

        def work():
            with self.open_dev(True) as d:
                d.set_label(n, label)
        self.run_async(work, self._after_edit(f"Etiqueta del disquete {n}: {label.upper()}"), "Cambiando etiqueta…")

    def act_import(self):
        n = self.require_slot()
        p = filedialog.askopenfilename(title="Imagen .img a importar") if n is not None else None
        if not p or not self.confirm(f"Se SOBRESCRIBIRÁ el disquete {n} con {os.path.basename(p)}."):
            return

        def work():
            with self.open_dev(True) as d:
                import_image(d, n, p)
        self.run_async(work, self._after_edit(f"{p} importado en el disquete {n}"), "Importando…")

    def act_export(self):
        n = self.require_slot()
        p = filedialog.asksaveasfilename(defaultextension=".img", initialfile=f"disquete{n:03d}.img") \
            if n is not None else None
        if not p:
            return

        def work():
            with self.open_dev() as d:
                export_image(d, n, p)
        self.run_async(work, lambda _: self.log(f"Disquete {n} exportado a {p}"), "Exportando…")

    def act_backup(self):
        p = filedialog.asksaveasfilename(defaultextension=".img", initialfile="copia_usb.img")
        if not p:
            return

        def work():
            with self.open_dev() as d:
                backup_device(d, p, self.set_progress)
        self.run_async(work, lambda _: self.log(f"Copia de seguridad guardada en {p}"), "Copiando USB…")

    def act_restore(self):
        p = filedialog.askopenfilename(title="Copia de seguridad a restaurar")
        if not p or not self.confirm("Se SOBRESCRIBIRÁN todos los disquetes con la copia seleccionada."):
            return

        def work():
            with self.open_dev(True) as d:
                restore_device(d, p, self.set_progress)
        self.run_async(work, self._after_edit("Copia restaurada"), "Restaurando…")

    # ------------------------------------------------------------ lotes
    def _fill_preview(self, res):
        self.batch_tree.delete(*self.batch_tree.get_children())
        for p in res.placements:
            gid = f"f{p.floppy}"
            if not self.batch_tree.exists(gid):
                self.batch_tree.insert("", "end", iid=gid, text=f"Disquete {p.floppy:03d}", open=True)
            note = {"too_big": "DEMASIADO GRANDE (omitido)", "renamed": "renombrado"}.get(p.status, "")
            if p.long_name:
                note = (note + " nombre largo (LFN)").strip()
            self.batch_tree.insert(gid, "end", text=p.dest if p.status != "too_big" else os.path.basename(p.src),
                                   values=(human_size(p.size), note))

    def _batch_args(self):
        src = self.src_var.get()
        if not src:
            raise GotekError("Elige la carpeta origen.")
        return src, int(self.start_var.get()), self.mode_var.get(), self.short_var.get()

    def act_preview(self):
        try:
            src, start, mode, short = self._batch_args()
            maxslot = None
            if self.path:
                with Device(self.path, slots=self._slots_limit()) as d:
                    maxslot = d.n_slots
        except GotekError as e:
            messagebox.showerror("Error", str(e))
            return

        def work():
            return run_batch(src, start, mode, short_names=short, max_slot=maxslot)

        def done(res):
            self._fill_preview(res)
            self.log(f"Vista previa: disquetes {res.first}..{res.last}, {len(res.placements)} archivos, "
                     f"{len(res.skipped)} omitidos, {len(res.long_names)} con nombre largo")
        self.run_async(work, done, "Calculando vista previa…")

    def act_batch(self):
        try:
            src, start, mode, short = self._batch_args()
        except GotekError as e:
            messagebox.showerror("Error", str(e))
            return
        if not self.path or not self.confirm(
                f"Se BORRARÁN los disquetes desde el {start} en adelante y se copiará «{src}» (modo {mode})."):
            return
        self.cancel_flag.clear()

        def work():
            with self.open_dev(True) as d:
                res = run_batch(src, start, mode, d, lambda p, i, n: (
                    self.set_progress(i, n), self.q.put(lambda p=p: self.log(f"{p.floppy:03d}: {p.dest}"))),
                    short_names=short, cancel=self.cancel_flag.is_set)
            idx = write_contents(res, src) if res.placements else ""
            return res, idx

        def done(r):
            res, idx = r
            self._fill_preview(res)
            self.log(("Cancelado. " if res.cancelled else "") + f"Copia terminada: disquetes {res.first}..{res.last}. Índice: {idx}")
            self.refresh_slots()
        self.run_async(work, done, "Copiando por lotes…")


def main() -> int:
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
