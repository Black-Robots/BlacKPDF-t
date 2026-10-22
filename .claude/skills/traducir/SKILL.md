---
name: traducir
description: Traducir los textos nuevos o cambiados de la interfaz de BlackPDF al inglés, al portugués de Brasil y al francés, con el glosario del proyecto para que los términos coincidan siempre. Úsala después de añadir o cambiar cualquier texto con self.tr(), QCoreApplication.translate o QT_TRANSLATE_NOOP.
---

# Traducir

Pasos, desde la raíz del repositorio (con `env -u PYTHONPATH .venv/bin/python`):

1. `scripts/i18n.py update`: extrae los textos del código a `translations/*.ts`.
2. `scripts/i18n.py pending build/i18n/nuevas.json`: lista lo que falta y deja una plantilla. Lo
   que ya está traducido igual en otro contexto se reutiliza solo y no aparece.
3. Rellena la plantilla siguiendo [glosario.md](glosario.md). Si son muchos textos, o quieres
   seguir con otra cosa mientras tanto, encárgaselo al subagente `traductor`.
4. `scripts/i18n.py fill build/i18n/nuevas.json`: aplica los textos y comprueba los marcadores.
   Tiene que terminar con «Todo traducido.».
5. `scripts/i18n.py build`: compila los `.qm`. Las pruebas fallan si no coinciden con los `.ts`.

Para corregir traducciones ya hechas, pon en el JSON el texto original con la versión buena: lo
que trae el JSON sustituye a lo que hubiera, en todos los contextos.

Reglas:
- Marcadores idénticos: `{0}`, `{page}`, `%1`, `(Ctrl+K)`... `fill` se niega si no coinciden.
- Portugués **de Brasil**, como el resto de la app: salvar, arquivo, senha, tela, planilha,
  você, pressione. Nada de guardar, ficheiro, ecrã, prima o folha de cálculo.
- Mismo registro que el español: tú/vosotros pasa a «you» en inglés, «você» en portugués y
  «vous» en francés; frases cortas y directas, como en la interfaz original.
- Comillas: el español usa «»; en inglés y portugués van “”, y en francés « » con espacio dentro.
  En francés, espacio antes de `:` `;` `?` `!`.
- Sin traducir: BlackPDF, PDF, Excel, CSV, STEP, OCR, nombres de teclas (Ctrl, Intro pasa a Enter).
- Un término nuevo que se vaya a repetir se añade al glosario.
