---
name: nueva-funcion
description: Receta completa para añadir o cambiar una función de BlackPDF, desde core hasta el commit (core sin Qt, prueba, interfaz, textos traducidos, captura, revisión y verificación). Úsala para cualquier función nueva, herramienta, panel o cambio de comportamiento.
argument-hint: "[qué hay que hacer]"
---

# Nueva función: $ARGUMENTS

Sigue CLAUDE.md siempre. Habla con el usuario en español, con avisos cortos de cada paso.

1. **Entender**: si el comportamiento de PyMuPDF no está claro, primero un experimento (skill
   `pymupdf`, o el subagente `explorador-pymupdf`). Si hay varias formas razonables y la elección es
   del usuario (dónde va en la interfaz, qué se ve), pregúntale con opciones y una recomendada.
2. **Core** (`blackpdf/core/<modulo>.py`): funciones puras, **sin Qt**, en coordenadas visibles.
   Errores previstos como subclases de `BlackPdfError` en `core/errors.py`, con su mensaje en
   `ui/errors.py`. Lo largo, en subproceso: `tasks.start("blackpdf.core.modulo:funcion", ...)`.
3. **Prueba de core** (`tests/test_<modulo>.py`): toda función de core lleva prueba, con PDFs
   generados en la propia prueba.
4. **Interfaz**: diálogo (`ToolDialog` o `_SingleSourceDialog` en `ui/dialogs/`, registrado en
   `ui/tools.py`), herramienta del visor (`ui/interaction.py`, `ui/annotation_bar.py`,
   `TOOL_SHORTCUTS`) o panel. Las ediciones pasan por `document.edit(etiqueta, operación, páginas)`
   para poder deshacer (`rewrite=True` si lo quitado no debe quedar en una revisión anterior).
   Textos con `self.tr()`, colores de `theme.py`, iconos Lucide, entrada en la paleta Ctrl+K
   (`main_window._commands`) si es una acción.
5. **Prueba de interfaz** en `tests/test_ui.py` (fixtures `window`, `opened`; `mouse`, `pump`, `wait_until`).
6. **Textos**: skill `traducir` (o el subagente `traductor`).
7. **Ver**: skill `captura`. Si la función necesita algo en un PDF, añádelo a `scripts/demo_pdf.py`.
8. **Revisar**: pide al subagente `revisor` que revise el cambio y corrige lo que encuentre.
9. **Verificar**: skill `verificar`, el script completo en VERDE.
10. **Commit** local, una función por commit, mensaje en español que explique el qué:
    `git -c user.email=hello@blackrobots.org commit`. Nunca añadas `ejemplo.pdf` (es del usuario).
    Nunca hagas push ni etiquetas: eso es `/publicar`, solo con el OK del usuario.

Al terminar, cuenta en pocas líneas qué hace la función, dónde está y cómo probarla.
