# Prompt maestro: USBFloppyManager — gestor de USB para Gotek (firmware original)

## Objetivo
Terminar y pulir **USBFloppyManager**, un programa multiplataforma (Linux + Windows) para gestionar un pendrive USB usado con un emulador de disquetera **Gotek con firmware original** (el USB se trata como una secuencia de disquetes FAT12 en crudo, NO como un FAT con archivos .img de FlashFloppy). Debe tener un **núcleo común**, una **CLI** y una **GUI de escritorio (tkinter)**.

Reemplaza y amplía el script Bash `GotekTool.sh` de Sebastian Mate, que copia un árbol de carpetas a disquetes virtuales montando `mount -o loop,offset=N*1536k,sizelimit=1440k`.

## Geometría (hecho de partida)
- Disquete N empieza en el offset `N × 1536 KiB` del dispositivo y ocupa `1440 KiB` (1.474.560 bytes).
- Cada slot es un FAT12 estándar de 1,44 MB (512 B/sector, 1 sector/cluster, 224 entradas raíz, 2 FAT de 9 sectores).
- Hay 96 KiB de hueco entre slots; nunca se escribe ahí.
- No usar `mount`: acceso directo al dispositivo/imagen, sin root salvo para abrir el dispositivo en crudo.

## Estado actual (prototipo ya funcional, en este repo)
- `usbfloppymanager/core.py`: `Device` (lectura/escritura de slots alineada a 512 B), `make_blank_image`, `is_valid_fat12`, `Slot` (trabaja sobre copia temporal con `pyfatfs` y vuelca con commit), `export_image`, `import_image`, `backup_device`.
- `usbfloppymanager/batch.py`: los 4 modos del script (1 aplanar, 2 aplanar+cambio por directorio, 3 conservar estructura, 4 carpetas raíz a disquetes distintos), orden natural (`sort -V`), vista previa (estimación) y copia real, renombrado de colisiones al aplanar, archivos demasiado grandes omitidos, índice `Contents NNN.txt`.
- `usbfloppymanager/devices.py`: detección de USB extraíbles (Linux vía /sys/block; Windows vía PowerShell, **sin probar**).
- `usbfloppymanager/cli.py` + `usbfloppy.py`: subcomandos `devices, list, ls, format, put, get, rm, export, import, backup, batch`.
- Verificado en una imagen simulada de 20 slots: los 4 modos en dry-run, copia real modo 3 y modo 1, ida y vuelta byte a byte con `get`.

## Pendiente (tu trabajo)
1. **GUI tkinter** (`usbfloppymanager/gui.py`, lanzador `usbfloppy-gui`):
   - Selector de dispositivo (USB detectados + abrir imagen .img) y botón de recarga.
   - Tabla de slots (nº, etiqueta, archivos, uso, válido/no) con barra de uso.
   - Explorador del slot seleccionado: árbol de carpetas/archivos, arrastrar y soltar (tkinterdnd2 opcional, con fallback a botón "Añadir"), extraer, borrar, nueva carpeta.
   - Acciones: formatear slot(s), importar/exportar .img, copia de seguridad completa del USB.
   - Pestaña "Copia por lotes": directorio origen, slot inicial, modo 1-4, **vista previa** en árbol (qué archivo cae en qué disquete) y ejecución con barra de progreso y registro. Operaciones largas en hilo aparte sin congelar la UI.
   - Confirmaciones claras antes de cualquier operación destructiva, mostrando dispositivo, tamaño y modelo.
   - La lógica debe vivir en `core`/`batch`; la GUI solo la llama.
2. **Seguridad**: rechazar por defecto dispositivos no extraíbles (con opción explícita para forzar), no permitir escribir en el disco del sistema, mensajes de error útiles cuando falten permisos.
3. **Corregir/pulir**:
   - `BrokenPipeError` al canalizar `usbfloppy ls ... | head` (capturar SIGPIPE/BrokenPipe en la CLI).
   - Etiqueta de volumen: implementar cambio de etiqueta (boot sector + entrada de volumen en la raíz).
   - Nombres de archivo: la firmware original probablemente solo entiende 8.3. Añadir opción `--short-names` que convierta a 8.3 únicos (estilo `NOMBRE~1.EXT`) y avisar si hay nombres largos. Investigar si pyfatfs genera entradas LFN y que no rompan en el Gotek.
   - Mejorar la estimación de la vista previa (clusters reales, entradas LFN) o, mejor, simular con una imagen en memoria usando el mismo código real para que preview == resultado.
   - Rendimiento: `Slot` hace un archivo temporal por slot; evaluar trabajar en memoria.
4. **Windows**: acceso a `\\.\PhysicalDriveN` (lecturas/escrituras alineadas a sector, bloqueo/desmontaje del volumen, elevación a administrador), detección de tamaño del dispositivo (el seek al final puede fallar: usar `IOCTL_DISK_GET_LENGTH_INFO` vía ctypes), y probar `devices.py`. Si no puedes probarlo en Windows, déjalo bien aislado, con tests de la parte pura y documenta qué falta validar.
5. **Tests** (pytest): imagen sintética de N slots; formateo y validación; escritura/lectura byte a byte; los 4 modos con árboles de prueba (incluye colisiones, archivos que no caben, muchos archivos pequeños que agotan las 224 entradas raíz, nombres con espacios/acentos); que la copia de un slot no toca los 96 KiB de hueco ni otros slots; verificación del FAT12 resultante con `fsck.fat -n` / `mtools` (`mdir`) si están disponibles (instalarlos en el entorno).
6. **Empaquetado**: `pyproject.toml` con entry points `usbfloppy` y `usbfloppy-gui`, README en español (instalación, uso, modos, advertencias, tabla de geometría), y script de PyInstaller para generar un `.exe` de Windows y un binario de Linux.

## Criterios de aceptación
- `pytest` pasa y cubre núcleo, lotes y CLI.
- Una imagen creada con `usbfloppy batch` abre sin errores en `fsck.fat -n` para cada slot y `mdir` lista lo esperado.
- Con la misma entrada, los modos 1-4 reproducen la distribución del script original `GotekTool.sh` (mismo orden natural y mismas reglas de cambio de disquete).
- Ninguna operación escribe fuera de `[N*1536K, N*1536K+1440K)` del slot objetivo.
- La GUI se puede arrancar, y todas sus acciones tienen equivalente en CLI.

## Reglas de trabajo
- Python 3.10+, dependencias mínimas (`pyfatfs`; `tkinterdnd2` opcional).
- Código y mensajes de usuario en español; commits pequeños y descriptivos.
- Empieza revisando el código existente y ejecutando una prueba rápida antes de tocar nada. Itera: tests primero donde sea posible.
- No inventes comportamiento del hardware: lo que no puedas verificar sin un Gotek real, anótalo en el README como "pendiente de validar en hardware".
