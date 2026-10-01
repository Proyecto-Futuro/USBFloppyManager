from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .batch import MODES, run_batch, write_contents
from .core import (Device, GotekError, Slot, backup_device, export_image, extract_tree,
                   import_image, restore_device)
from .names import is_short_name
from .devices import human_size as human, list_usb_devices


def confirm(msg: str, assume_yes: bool):
    if assume_yes:
        return
    if input(f"{msg}\nEscribe 'si' para continuar: ").strip().lower() not in ("si", "sí", "yes", "y"):
        raise GotekError("Cancelado por el usuario.")


def _describe(d: Device) -> str:
    return f"{d.path} ({human(d.size)}, {d.n_slots} slots)"


def _open(a, writable=False) -> Device:
    return Device(a.device, writable=writable, slots=a.slots, force=getattr(a, "force", False))


def cmd_devices(a):
    devs = list_usb_devices(include_fixed=a.all)
    if not devs:
        print("No se detectaron dispositivos USB extraíbles.")
    for d in devs:
        flags = ("  [SISTEMA]" if d.system else "") + ("" if d.removable else "  [no extraíble]")
        print(f"{d.path}\t{human(d.size)}\t{d.model}{flags}")


def cmd_list(a):
    with _open(a) as d:
        print(f"{d.path}: {d.n_slots} slots")
        for i in d.iter_info():
            if i.valid:
                print(f"{i.index:3d}  {i.label:<11} {i.files:3d} archivos  {human(i.used_bytes):>9} usados")
            elif a.all:
                print(f"{i.index:3d}  (sin FAT12 válido)")


def cmd_ls(a):
    with _open(a) as d, Slot(d, a.slot) as s:
        for e in s.walk():
            print(f"{'<DIR>' if e.is_dir else human(e.size):>10}  {e.path}")


def cmd_format(a):
    with _open(a, True) as d:
        confirm(f"Se BORRARÁN los slots {', '.join(map(str, a.slot))} de {_describe(d)}.", a.yes)
        for n in a.slot:
            d.format_slot(n, a.label or f"FD{n:03d}")
            print(f"Slot {n} formateado.")


def cmd_label(a):
    with _open(a, True) as d:
        d.set_label(a.slot, a.label)
        print(f"Slot {a.slot}: etiqueta «{a.label.upper()}».")


def _warn_long(names):
    if names:
        print(f"Aviso: {len(names)} nombre(s) no son 8.3 y usarán nombres largos (LFN); "
              "el firmware original puede no mostrarlos bien. Usa --short-names.", file=sys.stderr)


def cmd_put(a):
    long_names = []
    with _open(a, True) as d, Slot(d, a.slot, short_names=a.short_names) as s:
        for f in a.files:
            if os.path.isdir(f):
                raise GotekError(f"{f} es una carpeta; usa 'batch' para copiar árboles.")
            dest = (a.dest.strip("/") + "/" if a.dest else "") + os.path.basename(f)
            with open(f, "rb") as fh:
                final = s.write("/" + dest, fh.read())
            if not all(is_short_name(p) for p in final.split("/") if p):
                long_names.append(final)
            print(f"{f} -> slot {a.slot}:{final}")
    _warn_long(long_names)


def cmd_mkdir(a):
    with _open(a, True) as d, Slot(d, a.slot, short_names=a.short_names) as s:
        print(f"Creada {s.makedirs('/' + a.path.strip('/'))}")


def cmd_get(a):
    with _open(a) as d, Slot(d, a.slot) as s:
        path = "/" + a.path.strip("/")
        if s.isdir(path):
            out = extract_tree(s, path, a.out or ".")
            print(f"Extraídos {len(out)} archivos")
        else:
            data = s.read(path)
            out = a.out or os.path.basename(a.path)
            with open(out, "wb") as f:
                f.write(data)
            print(f"Guardado {out} ({human(len(data))})")


def cmd_rm(a):
    with _open(a, True) as d, Slot(d, a.slot) as s:
        s.remove("/" + a.path.strip("/"))
        print("Eliminado.")


def cmd_export(a):
    with _open(a) as d:
        export_image(d, a.slot, a.out)
        print(f"Slot {a.slot} -> {a.out}")


def cmd_import(a):
    with _open(a, True) as d:
        confirm(f"Se SOBRESCRIBIRÁ el slot {a.slot} de {_describe(d)}.", a.yes)
        import_image(d, a.slot, a.image)
        print(f"{a.image} -> slot {a.slot}")


def _bar(i, n):
    print(f"\r{i}/{n}", end="", flush=True)


def cmd_backup(a):
    with _open(a) as d:
        backup_device(d, a.out, _bar)
        print(f"\nCopia guardada en {a.out}")


def cmd_restore(a):
    with _open(a, True) as d:
        confirm(f"Se SOBRESCRIBIRÁN los slots de {_describe(d)} con {a.backup}.", a.yes)
        restore_device(d, a.backup, _bar)
        print("\nRestaurado.")


def cmd_batch(a):
    mode = a.mode
    if a.dry_run:
        n = None
        if a.device:
            with _open(a) as d:
                n = d.n_slots
        res = run_batch(a.src, a.start, mode, short_names=a.short_names, max_slot=n)
        print("(Vista previa: usa el mismo código que la copia real.)")
    else:
        with _open(a, True) as d:
            confirm(f"Se BORRARÁN los slots {a.start}.. de {_describe(d)} y se copiará {a.src}.", a.yes)
            res = run_batch(a.src, a.start, mode, d,
                            lambda p, i, n: print(f"{p.floppy:03d}: {p.dest}" +
                                                  ("  [DEMASIADO GRANDE]" if p.status == 'too_big' else "")),
                            short_names=a.short_names)
    print(f"\nDisquetes usados: {res.first}..{res.last}  ({len(res.placements)} archivos, {len(res.skipped)} omitidos)")
    if not a.short_names:
        _warn_long(res.long_names)
    if a.dry_run:
        for p in res.placements:
            print(f"{p.floppy:03d}  {p.dest}" + ("  [DEMASIADO GRANDE]" if p.status == "too_big" else ""))
    else:
        print("Índice:", write_contents(res, a.contents_dir))


def cmd_gui(a):
    from .gui import main as gui_main
    return gui_main()


def build_parser():
    p = argparse.ArgumentParser(prog="usbfloppy", description="Gestor de USB para Gotek (firmware original).")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help, dev=True, write=False):
        sp = sub.add_parser(name, help=help)
        if dev:
            sp.add_argument("device", help="/dev/sdX, \\\\.\\PhysicalDriveN o archivo .img")
            sp.add_argument("--slots", type=int, help="nº de slots si no se detecta el tamaño")
        if write:
            sp.add_argument("--force", action="store_true",
                            help="permitir dispositivos no extraíbles/montados (NUNCA el disco del sistema)")
        sp.set_defaults(fn=fn)
        return sp

    def yes(sp):
        sp.add_argument("-y", "--yes", action="store_true", help="no pedir confirmación")

    def short(sp):
        sp.add_argument("--short-names", action="store_true",
                        help="convertir nombres a 8.3 únicos (NOMBRE~1.EXT)")

    s = add("devices", cmd_devices, "listar USB extraíbles", dev=False)
    s.add_argument("-a", "--all", action="store_true", help="incluir discos no extraíbles")
    s = add("list", cmd_list, "listar slots"); s.add_argument("-a", "--all", action="store_true")
    s = add("ls", cmd_ls, "listar archivos de un slot"); s.add_argument("slot", type=int)
    s = add("format", cmd_format, "formatear slots", write=True); yes(s)
    s.add_argument("slot", type=int, nargs="+"); s.add_argument("--label")
    s = add("label", cmd_label, "cambiar la etiqueta de volumen de un slot", write=True)
    s.add_argument("slot", type=int); s.add_argument("label")
    s = add("put", cmd_put, "copiar archivos a un slot", write=True); s.add_argument("slot", type=int)
    s.add_argument("files", nargs="+"); s.add_argument("--dest", default=""); short(s)
    s = add("mkdir", cmd_mkdir, "crear carpeta en un slot", write=True); s.add_argument("slot", type=int)
    s.add_argument("path"); short(s)
    s = add("get", cmd_get, "extraer un archivo o carpeta"); s.add_argument("slot", type=int)
    s.add_argument("path"); s.add_argument("-o", "--out")
    s = add("rm", cmd_rm, "borrar archivo/carpeta", write=True); s.add_argument("slot", type=int); s.add_argument("path")
    s = add("export", cmd_export, "guardar un slot como .img"); s.add_argument("slot", type=int); s.add_argument("out")
    s = add("import", cmd_import, "escribir un .img en un slot", write=True); yes(s)
    s.add_argument("slot", type=int); s.add_argument("image")
    s = add("backup", cmd_backup, "copia de seguridad completa del USB"); s.add_argument("out")
    s = add("restore", cmd_restore, "restaurar una copia completa en el USB", write=True); yes(s)
    s.add_argument("backup")
    s = add("batch", cmd_batch, "copia por lotes (modos 1-4)", dev=False, write=True); yes(s); short(s)
    s.add_argument("-d", "--device", help="USB/imagen (opcional con --dry-run)")
    s.add_argument("--slots", type=int)
    s.add_argument("src"); s.add_argument("start", type=int); s.add_argument("mode", type=int, choices=sorted(MODES))
    s.add_argument("--dry-run", action="store_true", help="solo vista previa"); s.add_argument("--contents-dir", default=".")
    add("gui", cmd_gui, "abrir la interfaz gráfica", dev=False)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "batch" and not a.dry_run and not a.device:
            raise GotekError("Indica el dispositivo (o usa --dry-run).")
        return a.fn(a) or 0
    except GotekError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # `usbfloppy ls ... | head`: salida cerrada por el lector; no es un error
        sys.stdout = open(os.devnull, "w")  # evita un segundo error al vaciar stdout al salir
        return 0


if __name__ == "__main__":
    sys.exit(main())
