"""PDF de prueba con un poco de todo, para probar BlackPDF a mano o en capturas.

    python scripts/demo_pdf.py [SALIDA.pdf]      (por defecto, build/prueba.pdf)

Lleva una tabla (Tablas a Excel), un plano con capas y escala 1:50 (Capas, Medir),
etiquetas de página («P-1», «A-101») y dos adjuntos, uno de ellos una pieza STEP.
Al añadir aquí una función nueva que necesite algo en el PDF, se añade a este archivo.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from blackpdf.core.measure import PT_MM  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "prueba.pdf"
out.parent.mkdir(parents=True, exist_ok=True)
step = (ROOT / "blackpdf" / "resources" / "model3d" / "placa.step").read_bytes()
doc = pymupdf.open()

# Página 1: presupuesto con una tabla (Tablas a Excel).
page = doc.new_page()
page.insert_text((72, 80), "Presupuesto de reforma", fontsize=22, fontname="hebo")
page.insert_text((72, 104), "Prueba de «Tablas a Excel»: la tabla sale con los números listos para sumar.", fontsize=10)
rows = [["Partida", "Cantidad", "Precio (EUR)", "Total (EUR)"],
        ["Ladrillo perforado", "1.200", "0,35", "420,00"],
        ["Cemento (saco 25 kg)", "40", "6,10", "244,00"],
        ["Azulejo cocina (m²)", "18,5", "24,90", "460,65"],
        ["Mano de obra oficial (h)", "16", "22,50", "360,00"],
        ["Total", "", "", "1.484,65"]]
for r, row in enumerate(rows):
    for c, text in enumerate(row):
        rect = pymupdf.Rect(72 + c * 115, 130 + r * 26, 72 + (c + 1) * 115, 130 + (r + 1) * 26)
        page.draw_rect(rect, color=(0.3, 0.3, 0.3), width=0.6, fill=(0.93, 0.93, 0.93) if r == 0 else None)
        page.insert_text((rect.x0 + 5, rect.y0 + 17), text, fontsize=9.5,
                         fontname="hebo" if r in (0, len(rows) - 1) else "helv")

# Página 2: plano con capas y escala 1:50 (Capas y Medir).
plan = doc.new_page(width=842, height=595)
plan.insert_text((60, 50), "Planta de vivienda — escala 1:50", fontsize=16, fontname="hebo")
layer = {name: doc.add_ocg(name, on=on) for name, on in
         (("Muros", True), ("Cotas", True), ("Mobiliario", False), ("Electricidad", True), ("Fontanería", True))}
# 1:50 → 1 m son 20 mm de papel. La vivienda mide 9,5 × 6,5 m.
m = 1000 / 50 / PT_MM
x0, y0 = 80.0, 90.0
outer = pymupdf.Rect(x0, y0, x0 + 9.5 * m, y0 + 6.5 * m)
plan.draw_rect(outer, color=(0, 0, 0), width=5, oc=layer["Muros"])
plan.draw_line((x0 + 4.5 * m, y0), (x0 + 4.5 * m, y0 + 3.5 * m), color=(0, 0, 0), width=3, oc=layer["Muros"])
plan.draw_line((x0 + 4.5 * m, y0 + 3.5 * m), (x0 + 9.5 * m, y0 + 3.5 * m), color=(0, 0, 0), width=3, oc=layer["Muros"])
for text, (tx, ty) in (("Salón", (1.8, 3.0)), ("Cocina", (6.6, 1.8)), ("Dormitorio", (6.2, 5.2))):
    plan.insert_text((x0 + tx * m, y0 + ty * m), text, fontsize=13)
plan.draw_line((x0, y0 - 18), (outer.x1, y0 - 18), color=(0.8, 0.1, 0.1), width=0.8, oc=layer["Cotas"])
plan.insert_text(((x0 + outer.x1) / 2 - 15, y0 - 22), "9,50", fontsize=10, color=(0.8, 0.1, 0.1), oc=layer["Cotas"])
plan.draw_rect(pymupdf.Rect(x0 + 0.6 * m, y0 + 4.2 * m, x0 + 3.0 * m, y0 + 5.2 * m), color=(0.4, 0.4, 0.4),
               fill=(0.85, 0.85, 0.85), oc=layer["Mobiliario"])
for i in range(5):
    plan.draw_circle((x0 + (1 + i * 1.8) * m, y0 + 0.4 * m), 5, color=(0.9, 0.55, 0), width=1.5, oc=layer["Electricidad"])
plan.draw_line((x0 + 5 * m, y0 + 3.2 * m), (x0 + 9.2 * m, y0 + 3.2 * m), color=(0, 0.4, 1), width=2,
               oc=layer["Fontanería"])
catalog = doc.pdf_catalog()
doc.xref_set_key(catalog, "OCProperties/D/Order",
                 f"[{layer['Muros']} 0 R {layer['Cotas']} 0 R {layer['Mobiliario']} 0 R "
                 f"[(Instalaciones) {layer['Electricidad']} 0 R {layer['Fontanería']} 0 R]]")
height = plan.mediabox.height
doc.xref_set_key(plan.xref, "VP", f"[<</Type/Viewport/BBox[0 0 842 {height:g}]/Measure<</Type/Measure"
                                  f"/Subtype/RL/R(1:50)/X[<</Type/NumberFormat/U(m)/C {PT_MM * 50 / 1000:.8f}>>]>>>>]")

# Etiquetas de página: «P-1» y «A-101», como un juego de planos.
doc.set_page_labels([{"startpage": 0, "prefix": "P-", "style": "D", "firstpagenum": 1},
                     {"startpage": 1, "prefix": "A-", "style": "D", "firstpagenum": 101}])

# Adjuntos: la pieza en STEP y las mediciones.
doc.embfile_add("placa.step", step, filename="placa.step", desc="Pieza en 3D (se puede insertar como vista 3D)")
doc.embfile_add("mediciones.csv", "Estancia;Superficie (m²)\nSalón;15,75\nCocina;17,50\nDormitorio;15,00\n"
                .encode("utf-8-sig"), filename="mediciones.csv", desc="Mediciones de la vivienda")
doc.set_metadata({"title": "Prueba de BlackPDF", "author": "BlackPDF"})
doc.save(out, garbage=3, deflate=True)
print(out)
