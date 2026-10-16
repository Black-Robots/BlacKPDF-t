"""Punto de entrada para el ejecutable empaquetado (PyInstaller)."""

import sys

from blackpdf.app import main

if __name__ == "__main__":
    sys.exit(main())
