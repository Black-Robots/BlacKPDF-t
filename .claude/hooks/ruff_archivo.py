"""Tras editar un .py del proyecto, ruff revisa ese archivo (errores de verdad: nombres sin definir, sintaxis).

Si encuentra algo, sale con 2 para que Claude lo vea y lo arregle en el momento.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

data = json.load(sys.stdin)
path = Path((data.get("tool_input") or {}).get("file_path") or "")
root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
if path.suffix != ".py" or not path.exists() or not path.resolve().is_relative_to(root):
    sys.exit(0)
ruff = shutil.which("ruff")
uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
if ruff:
    command = [ruff, "check", "--select", "F,E9", str(path)]
elif Path(uv).exists():
    command = [uv, "tool", "run", "ruff", "check", "--select", "F,E9", str(path)]
else:
    sys.exit(0)
result = subprocess.run(command, cwd=root, capture_output=True, text=True)
if result.returncode != 0:
    print(result.stdout + result.stderr, file=sys.stderr)
    sys.exit(2)
