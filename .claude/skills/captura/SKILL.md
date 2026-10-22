---
name: captura
description: Ver cómo queda de verdad un cambio de la interfaz de BlackPDF con capturas sin pantalla (una escena concreta o todos los diálogos) y revisarlas frente a las reglas de diseño. Úsala tras cualquier cambio visible, antes de darlo por bueno.
---

# Captura

Escena concreta (ajustes temporales, no toca los del usuario):

```bash
env -u PYTHONPATH .venv/bin/python scripts/demo_pdf.py                     # build/prueba.pdf con de todo
env -u PYTHONPATH .venv/bin/python scripts/captura.py SALIDA.png --pdf build/prueba.pdf \
    [--pagina 2] [--ajuste ancho] [--herramienta measure] [--panel attachments|layers|outline|pages|no] \
    [--comentarios] [--clics "80,90;200,90"] [--raton 348,180] [--dialogo tables] \
    [--desplegable sellos|escala] [--claro] [--tamano 900x700]
```

En lote: `scripts/screenshot_dialogs.py DIR PDF [--light]` (todos los diálogos) y
`scripts/screenshot.py DIR [PDF] [--light]` (inicio, documento, búsqueda y paleta).

Guarda las capturas en el directorio temporal de la sesión, nunca en el repo, y míralas con Read.

Qué revisar:
- Tema oscuro y claro (`--claro`), y una ventana estrecha (`--tamano 900x700`).
- Colores solo de `blackpdf/ui/theme.py`; acento rojo #E5484D para lo activo.
- Espaciado múltiplo de 4, bordes redondeados de 8 px, alineaciones limpias, nada solapado.
- Textos en español, sin cortar ni salirse (también los largos traducidos), sin emojis.
- Iconos Lucide coherentes con el resto (los nuevos, de `https://unpkg.com/lucide-static@0.460.0/icons/NOMBRE.svg`).
- Lo que se dibuja en el PDF: derecho también en páginas giradas, nítido con zoom.

Las capturas son para revisar. Nunca las enseñes como novedad ni las uses en la web sin el OK del usuario.
