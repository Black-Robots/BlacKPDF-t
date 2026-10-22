"""Antes de un «git commit», scripts/verificar.py --rapido: con algo en rojo, el commit no se hace."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

data = json.load(sys.stdin)
command = (data.get("tool_input") or {}).get("command") or ""
if not re.search(r"\bgit\b[^|;&\n]*\scommit\b", command):
    sys.exit(0)
root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
python = root / ".venv" / "bin" / "python"
if not python.exists():
    python = root / ".venv" / "Scripts" / "python.exe"
if not python.exists():
    sys.exit(0)
env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
result = subprocess.run([str(python), "scripts/verificar.py", "--rapido"], cwd=root, env=env,
                        capture_output=True, text=True)
if result.returncode != 0:
    print("Commit bloqueado: la verificación rápida está en ROJO.\n" + result.stdout[-4000:], file=sys.stderr)
    sys.exit(2)
