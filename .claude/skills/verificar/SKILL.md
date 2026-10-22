---
name: verificar
description: Comprobación de BlackPDF antes de cada commit o de dar algo por terminado (ruff, traducciones, pruebas y autocomprobación) con un único resultado VERDE o ROJO. Úsala siempre antes de hacer commit, de decir que algo funciona o de publicar.
---

# Verificar

```bash
env -u PYTHONPATH .venv/bin/python scripts/verificar.py            # todo (unos 3 min)
env -u PYTHONPATH .venv/bin/python scripts/verificar.py --rapido   # sin pruebas de interfaz (unos 20 s)
```

- Mientras iteras, `--rapido` o una prueba suelta:
  `env -u PYTHONPATH QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -p no:warnings tests/test_x.py -k nombre`.
- Antes de cada commit de una función: el script **completo** en VERDE.
- Con ROJO no hay commit. Arregla y vuelve a verificar.
- Nunca encadenes `pytest ... | tail && git commit`: `tail` esconde el código de salida y el
  commit se hace aunque fallen pruebas. Usa el script, que termina con 0 solo si todo pasa.
- El `env -u PYTHONPATH` evita que un PYTHONPATH ajeno (ROS) cuele paquetes que no son del venv.
- Si una prueba de interfaz falla de forma rara, mira antes si es la propia prueba: ventanas
  sin mostrar (`isVisibleTo` en vez de `isVisible`), el autodesplazamiento al arrastrar cerca
  del borde, o una página de PyMuPDF que se libera (ver la skill `pymupdf`).
