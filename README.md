# USBFloppyManager

Gestor multiplataforma (Linux + Windows) para un pendrive USB usado con un emulador de disquetera
**Gotek con firmware original**: el USB se trata como una secuencia de disquetes FAT12 en crudo
(no son archivos `.img` dentro de un FAT, como en FlashFloppy). Incluye núcleo común, **CLI** y **GUI (tkinter)**.
Sustituye y amplía `GotekTool.sh` de Sebastian Mate sin usar `mount`.

## Geometría

| Concepto | Valor |
|---|---|
| Inicio del disquete N | `N × 1536 KiB` |
| Tamaño de cada disquete | `1440 KiB` (1.474.560 bytes) |
| Hueco entre disquetes | 96 KiB (**nunca se escribe**) |
| Sistema de ficheros | FAT12 estándar: 512 B/sector, 1 sector/cluster, 224 entradas raíz, 2 FAT de 9 sectores |
| Zona de datos | 2847 clusters (1.457.664 bytes) |

Los accesos al dispositivo son siempre de 1440 KiB en offsets múltiplos de 1536 KiB (ambos múltiplos de 4 KiB),
válidos para dispositivos en crudo de Windows. Ninguna operación escribe fuera de `[N·1536K, N·1536K+1440K)`.

## Instalación

```bash
pip install .            # instala los comandos usbfloppy y usbfloppy-gui
pip install ".[dnd]"     # opcional: arrastrar y soltar en la GUI (tkinterdnd2)
# La GUI usa el tema moderno sv-ttk (claro/oscuro, botón ☾/☀); se instala automáticamente.
```
Requiere Python ≥ 3.10. La GUI necesita `tkinter` (Debian/Ubuntu: `sudo apt install python3-tk`).

Acceder a un dispositivo en crudo exige permisos: en Linux `sudo usbfloppy ...` (o grupo `disk`); en Windows,
ejecutar como administrador (la GUI tiene un botón «Reiniciar como administrador»).

## Uso (CLI)

`DISPOSITIVO` puede ser `/dev/sdX`, `\\.\PhysicalDriveN` o un archivo de imagen. Usa `--slots N` si no se detecta el tamaño.

```bash
usbfloppy devices [-a]                        # USB extraíbles (-a: todos los discos, marcando el del sistema)
usbfloppy list DISP [-a]                      # slots, etiqueta, archivos y uso real
usbfloppy ls DISP 12                          # árbol de archivos del slot 12
usbfloppy format DISP 3 4 5 [--label X] [-y]  # formatear slots
usbfloppy label DISP 3 JUEGOS                 # cambiar etiqueta (boot sector + entrada de volumen)
usbfloppy put DISP 3 a.txt b.bin [--dest dir] [--short-names]
usbfloppy mkdir DISP 3 carpeta [--short-names]
usbfloppy get DISP 3 /dir/a.txt -o a.txt      # también carpetas completas (-o directorio)
usbfloppy rm DISP 3 /dir
usbfloppy export DISP 3 slot3.img             # un slot -> .img
usbfloppy import DISP 3 slot3.img [-y]        # .img -> slot
usbfloppy backup DISP copia.img               # copia completa (misma disposición que el USB)
usbfloppy restore DISP copia.img [-y]
usbfloppy batch [-d DISP] ORIGEN SLOT_INICIAL MODO [--dry-run] [--short-names] [-y]
usbfloppy gui
```
Las operaciones destructivas piden confirmación (`-y` para omitirla) mostrando dispositivo y tamaño.

### Copia por lotes (modos de `GotekTool.sh`)

Los archivos se ordenan de forma natural (`sort -V`) por ruta.

| Modo | Comportamiento |
|---|---|
| 1 | Aplanar: todo en la raíz de cada disquete; las colisiones se renombran (`x_2.txt`) |
| 2 | Aplanar, cambiando de disquete en cada directorio |
| 3 | Conservar la estructura de directorios |
| 4 | Conservar estructura; cada carpeta raíz va a un disquete distinto |

Cuando un archivo no cabe se pasa al siguiente disquete; si es mayor que un disquete vacío se omite y se avisa.
Se genera `Contents NNN.txt` (disquete, nº de archivo, origen). **La vista previa (`--dry-run`, o el botón de la GUI)
ejecuta el mismo código que la copia real sobre imágenes en memoria**, así que coincide con el resultado
(clusters reales, entradas de directorio y nombres largos incluidos).

## Interfaz gráfica

`usbfloppy-gui` (o `python usbfloppy_gui.py`): selector de dispositivo (USB detectados o imagen) con recarga;
tabla de slots con barra de uso; explorador del slot (añadir archivos/carpetas, arrastrar y soltar con
`tkinterdnd2`, extraer, borrar, nueva carpeta); formatear, etiqueta, importar/exportar `.img`, copia de seguridad y
restauración; pestaña «Copia por lotes» con vista previa en árbol, progreso, cancelación y registro. Las operaciones
largas corren en un hilo. Toda acción de la GUI tiene su equivalente en la CLI.

## Seguridad

- Sólo se escribe por defecto en **archivos de imagen** y dispositivos **extraíbles/USB sin montar**.
- Nunca se escribe en el disco que contiene el sistema (ni con `--force`).
- Particiones, discos no extraíbles o con volúmenes montados se rechazan salvo `--force` (GUI: «Forzar»).
- Los errores de permisos explican cómo solucionarlos. **Haz una copia de seguridad (`backup`) antes de operar.**
  `batch` formatea cada slot que usa.

## Nombres de archivo (8.3 / LFN)

El firmware original probablemente sólo entiende nombres 8.3. `--short-names` (GUI: «Nombres 8.3») convierte cada
componente a un 8.3 único en mayúsculas (`DOCUME~1.TXT`, acentos transliterados). Sin esa opción los nombres 8.3
válidos se guardan en mayúsculas y los demás como **nombres largos (LFN)**: pyfatfs sí genera entradas LFN
(con alias `~N`; `fsck.fat` y `mdir` las leen bien), pero consumen entradas de la raíz (224 máx.) y se avisa si hay.

## Tests

```bash
pip install ".[test]"
sudo apt install dosfstools mtools xvfb   # opcional: verifica con fsck.fat -n y mdir; test de la GUI
pytest
```
Cubren núcleo, lotes (4 modos, colisiones, archivos que no caben, raíz agotada, nombres con espacios/acentos,
huecos intactos, preview == resultado), CLI (incluye `| head`), seguridad de dispositivos y la parte pura de Windows.

## Binarios (PyInstaller)

```bash
pip install ".[build]"
python build_pyinstaller.py    # ejecutar en cada SO: .exe en Windows, binario en Linux (carpeta dist/)
```

## Pendiente de validar

- **Hardware**: no se ha probado con un Gotek real. Falta confirmar que el firmware original lista bien (a) los
  discos creados, (b) carpetas, (c) nombres LFN (se recomienda `--short-names`) y (d) la etiqueta de volumen.
- **Windows**: `devices.py` (PowerShell), `winraw.py` (`\\.\PhysicalDriveN`, `IOCTL_DISK_GET_LENGTH_INFO`,
  bloqueo/desmontaje de volúmenes, elevación UAC) y el `.exe` **no se han probado en Windows**. La lógica pura
  (parseo de discos, alineación, tamaño) sí tiene tests.
- `GotekTool.sh` original no estaba disponible: los modos 1-4 mantienen la lógica del prototipo (orden natural,
  reglas de cambio de disquete); conviene compararlos con el script con una carpeta real.
- Sólo se probó el arranque de la GUI en un entorno sin pantalla (Xvfb); falta uso interactivo real y DnD.

## AppImage y releases

`packaging/build_appimage.sh` genera `USBFloppyManager-x86_64.AppImage` (sin argumentos abre la GUI; con argumentos
funciona como la CLI: `./USBFloppyManager-x86_64.AppImage list /dev/sdX`). Para abrir un USB en crudo hace falta root:
`sudo ./USBFloppyManager-x86_64.AppImage` (si no abre ventana: `xhost +si:localuser:root` y `sudo -E ...`).

El workflow `.github/workflows/release.yml` ejecuta los tests en cada push y, al subir un tag `vX.Y.Z`, compila el
AppImage y binario de CLI `usbfloppy-linux-x86_64` (Linux) y los `.exe` (Windows) y los adjunta a la release de GitHub:

```bash
git tag v0.3.0 && git push origin v0.3.0   # el tag debe coincidir con __version__
```
El `.exe` y el AppImage compilado en CI no están probados con hardware real (ver «Pendiente de validar»).
