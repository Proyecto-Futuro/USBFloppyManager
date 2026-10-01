#!/usr/bin/env bash
# Genera USBFloppyManager-x86_64.AppImage. Requisitos: Python con tkinter, `pip install . pyinstaller`,
# y appimagetool (se descarga si no está en el PATH). Sin argumentos abre la GUI; con argumentos, la CLI.
set -euo pipefail
cd "$(dirname "$0")/.."
ARCH="${ARCH:-x86_64}"
rm -rf build dist AppDir
python -m PyInstaller --noconfirm --clean --onedir --name usbfloppymanager \
    --collect-all pyfatfs --collect-all fs --collect-all sv_ttk --collect-all tkinterdnd2 usbfloppy_app.py
mkdir -p AppDir/usr
cp -r dist/usbfloppymanager AppDir/usr/bin
cp packaging/usbfloppymanager.desktop AppDir/
cp packaging/usbfloppymanager.png AppDir/
cat > AppDir/AppRun <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/usbfloppymanager" "$@"
RUN
chmod +x AppDir/AppRun
if ! command -v appimagetool >/dev/null; then
    curl -fsSL -o /tmp/appimagetool \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x /tmp/appimagetool
    TOOL="/tmp/appimagetool --appimage-extract-and-run"
else
    TOOL=appimagetool
fi
ARCH="$ARCH" $TOOL AppDir "USBFloppyManager-${ARCH}.AppImage"
echo "Listo: USBFloppyManager-${ARCH}.AppImage"
