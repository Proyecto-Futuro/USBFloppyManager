"""Punto de entrada único (AppImage): sin argumentos abre la GUI; con argumentos, la CLI."""
import sys

if len(sys.argv) > 1:
    from usbfloppymanager.cli import main
else:
    from usbfloppymanager.gui import main
sys.exit(main())
