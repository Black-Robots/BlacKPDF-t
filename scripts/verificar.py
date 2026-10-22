"""Comprobación completa antes de dar un cambio por terminado (y antes de cada commit).

    python scripts/verificar.py            ruff, traducciones, pruebas y autocomprobación
    python scripts/verificar.py --rapido   lo mismo sin las pruebas de interfaz (las más lentas)

Termina con 0 solo si todo está en verde; si algo falla, enseña el final de su salida.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINT_PATHS = ("blackpdf", "tests", "scripts", "native")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # Un PYTHONPATH ajeno (ROS, por ejemplo) mete paquetes que no son los del venv.
    env.pop("PYTHONPATH", None)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def _ruff() -> list[str] | None:
    ruff = shutil.which("ruff")
    if ruff:
        return [ruff, "check", "--select", "F,E9", *LINT_PATHS]
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if Path(uv).exists():
        return [uv, "tool", "run", "ruff", "check", "--select", "F,E9", *LINT_PATHS]
    return None


def main() -> int:
    quick = "--rapido" in sys.argv
    python = sys.executable
    pytest = [python, "-m", "pytest", "-q", "-p", "no:warnings"] + (["--ignore=tests/test_ui.py"] if quick else [])
    steps = [
        ("ruff", _ruff()),
        ("traducciones", [python, "scripts/i18n.py", "check"]),
        ("pruebas" + (" (sin interfaz)" if quick else ""), pytest),
        ("autocomprobación", [python, "-m", "blackpdf", "--self-test"]),
    ]
    failed = []
    for name, command in steps:
        if command is None:
            print(f"--   {name}: no disponible (instala ruff o uv)")
            continue
        start = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=_env(), capture_output=True, text=True)
        seconds = time.monotonic() - start
        if result.returncode == 0:
            print(f"OK   {name} ({seconds:.0f} s)")
            continue
        failed.append(name)
        print(f"MAL  {name} ({seconds:.0f} s)")
        output = (result.stdout + result.stderr).strip().splitlines()
        for line in output[-40:]:
            print("     " + line)
    print()
    if failed:
        print("ROJO: " + ", ".join(failed) + ". No hagas commit hasta arreglarlo.")
        return 1
    print("VERDE: todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
