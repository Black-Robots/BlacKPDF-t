---
name: pymupdf
description: Trampas de PyMuPDF/MuPDF ya encontradas en BlackPDF (coordenadas y giro, apariencias que MuPDF redibuja, sellos, guardado incremental, capas, adjuntos, objetos que se liberan) y cómo esquivarlas. Úsala antes de escribir o depurar código de blackpdf/core o pruebas que toquen PDFs.
user-invocable: false
paths:
  - "blackpdf/core/**"
  - "tests/**"
---

# PyMuPDF en BlackPDF: lo que ya se sabe

Antes de dar por buena una idea sobre cómo se comporta PyMuPDF, compruébala con un experimento
pequeño en el directorio temporal (o pide al subagente `explorador-pymupdf`). Cuando descubras
algo nuevo que haya costado, añádelo aquí en una línea.

## Objetos que se liberan
- `doc[0].first_annot`, `pymupdf.open(p)[0].get_text()`: la página (o el documento) temporal se
  libera y la anotación queda «not bound to any page» o con datos vacíos. Guarda la página en una
  variable: `page = doc[0]; annot = page.first_annot`. Igual con documentos: `with pymupdf.open(p) as d:`.

## Coordenadas
- Core y la interfaz trabajan en coordenadas **visibles** (`page.rect`, ya girada).
- Visibles → sin girar: `p * page.derotation_matrix`. Sin girar → visibles: `p * page.rotation_matrix`.
- Visibles → espacio PDF (y hacia arriba): `p * (page.derotation_matrix * ~page.transformation_matrix)`.
- `page.add_*_annot(...)` recibe coordenadas **sin girar** (PyMuPDF quita el giro un momento).
- `annot.set_rect(r)` espera coordenadas **sin girar**; `annot.rect`, `annot.vertices` y
  `page.get_cdrawings()` también vienen sin girar.
- Un `/Rect` escrito a mano con `xref_set_key` va en espacio PDF.

## Apariencias (/AP) que MuPDF redibuja
- `annot.update()` vuelve a generar la apariencia de los tipos que MuPDF sabe dibujar (línea,
  polígono, polilínea, formas, texto libre...) si algo la marcó como sucia (`set_colors`,
  `set_info` tras reabrir, `set_rect`...). Una apariencia propia se pierde.
- Orden correcto con apariencia propia: colores, autor, contenido y nombre → `update()` →
  `annot._setAP(stream)` → `BBox`, `Matrix` y `Resources` con `xref_set_key`. Nada de `update()` después.
- Las medidas (`core/measure.py`) se redibujan con `measure.redraw(page, annot)` después de
  cualquier `update()`; `core/comments._stamp` ya lo hace.
- Sellos: MuPDF solo redibuja los de `/Name` estándar (Approved, Draft...). Con un nombre propio
  (`/BlackPDF.approved`) respeta la apariencia siempre.
- `add_stamp_annot` ajusta el rectángulo a la proporción de su sello en inglés: fíjalo después con `set_rect`.
- Apariencia girada: `BBox` en el sentido en que se ve y `Matrix` = giro de `page.rotation`
  (`[cos sin -sin cos 0 0]`): el visor ajusta el BBox transformado al `/Rect`.
- Fuentes estándar en apariencias: WinAnsi (cp1252). Escapa `( ) \` y escribe >127 en octal
  (`annotations.pdf_string`). Mejor la fuente como diccionario directo en `Resources` que como
  objeto: si no, cada redibujado deja un objeto suelto.
- `pymupdf.get_text_length(texto)` mide mal las cadenas con tildes: mide letra a letra
  (`annotations.text_width`).

## Guardado
- PyMuPDF no guarda de forma incremental un documento abierto desde memoria, y MuPDF rompe el
  archivo si se guarda dos veces en incremental con el mismo objeto. BlackPDF lo resuelve en
  `core/document.incremental_update`: abre los bytes originales y copia solo los objetos que han
  cambiado (los números de objeto se conservan al deshacer, porque `tobytes()` sin `garbage` no renumera).
- Si MuPDF tuvo que reconstruir el archivo al abrirlo (`doc.is_repaired`), no admite incremental.
- Al guardar un PDF con adjuntos, MuPDF le pone `/PageMode /UseAttachments`.
- `doc.tobytes()` no guarda qué capas se ven en ese momento (el estado de `set_layer_ui_config`).

## Capas (OCG)
- `set_layer_ui_config(n, 0|2)` cambia lo que se dibuja al momento, pero no el archivo; las
  `DisplayList` hechas antes no se enteran (hay que vaciar la caché del render).
- `get_ocgs()[xref]["on"]` refleja el estado actual; `get_layer(-1)` es la vista inicial guardada.
- Las capas bloqueadas no cambian y los grupos de opción única los gestiona MuPDF.

## Diccionarios y flujos a bajo nivel
- `xref_set_key(x, "Clave", "null")` no borra la clave: usa `mupdf.pdf_dict_dels(obj, "Clave")`.
- `fz_buffer_extract` vacía los flujos de un documento en memoria: usa `fz_buffer_extract_copy`.
- `doc.xref_object(x)` lanza una excepción en objetos libres: envuélvelo en `try`.
- Para que la lista de anotaciones de MuPDF vea una anotación creada a mano, `pdf_create_annot_raw`.
- `doc.scrub()` deja `/JS ()` vacíos y `/PageMode /UseAttachments`: `sanitize._finish_scrub`.

## Otros
- `page.find_tables()` escribe un aviso por la salida estándar: llama antes a `pymupdf.no_recommend_layout()`.
- PyMuPDF es monohilo: nada de usarlo desde hilos. Lo largo va en un subproceso con
  `tasks.start("blackpdf.core.modulo:funcion", ...)`.
- pyHanko marca como «OTHER» cualquier anotación añadida tras firmar; BlackPDF decide si solo son
  comentarios con `signatures.comments_only_after`.
