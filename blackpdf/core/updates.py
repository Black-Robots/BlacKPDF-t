"""Versiones nuevas de BlackPDF: consultar GitHub, descargar y comprobar la descarga.

Es lo único de BlackPDF que se conecta a internet, y solo si el usuario lo permite
(como mucho una vez al mes) o lo pide: se consulta la API pública de GitHub sin enviar
ningún dato propio.
Cada descarga se compara con la suma SHA-256 publicada en la misma versión.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .. import REPOSITORY, __version__
from .errors import UpdateCheckError, UpdateDownloadError, UpdateVerifyError, WriteError

API_LATEST = REPOSITORY.replace("https://github.com/", "https://api.github.com/repos/") + "/releases/latest"
CHECKSUMS = ("SHA256SUMS.txt", "SHA256SUMS")
CHUNK = 256 * 1024

KIND_WINDOWS = "windows"
"""Instalado con el instalador de Windows: se actualiza solo."""
KIND_APPIMAGE = "appimage"
"""AppImage: se sustituye el archivo."""
KIND_DEB = "deb"
"""Paquete .deb: se abre con el instalador del sistema (necesita la contraseña)."""
KIND_MANUAL = "manual"
"""Desde el código fuente u otra instalación: solo se abre la página de descarga."""


@dataclass
class Release:
    version: str
    page: str
    notes: str = ""
    published: str = ""
    assets: dict[str, str] = field(default_factory=dict)
    """Nombre del archivo -> dirección de descarga."""


def parse_version(text: str) -> tuple[int, ...]:
    """«v1.2.10» -> (1, 2, 10). Lo que no sea numérico al final (-beta...) se ignora."""
    numbers = re.findall(r"\d+", text.strip().lstrip("vV").split("-")[0].split("+")[0])
    return tuple(int(n) for n in numbers) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def parse_release(data: dict) -> Release:
    """Versión publicada a partir de la respuesta de la API de GitHub."""
    assets = {
        str(item.get("name", "")): str(item.get("browser_download_url", ""))
        for item in data.get("assets") or []
        if item.get("name") and item.get("browser_download_url")
    }
    return Release(
        version=str(data.get("tag_name") or "").lstrip("vV"),
        page=str(data.get("html_url") or REPOSITORY + "/releases/latest"),
        notes=str(data.get("body") or ""),
        published=str(data.get("published_at") or ""),
        assets=assets,
    )


def asset_for(release: Release, kind: str) -> str | None:
    """Nombre del archivo de la versión que corresponde a un tipo de instalación."""
    tests = {
        KIND_WINDOWS: lambda name: name.endswith("-Setup.exe"),
        KIND_APPIMAGE: lambda name: name.endswith(".AppImage") and "x86_64" in name,
        KIND_DEB: lambda name: name.endswith("_amd64.deb"),
    }
    test = tests.get(kind)
    if test is None:
        return None
    return next((name for name in sorted(release.assets) if test(name)), None)


def parse_checksums(text: str) -> dict[str, str]:
    """Líneas de sha256sum («suma  archivo» o «suma *archivo») -> {archivo: suma}."""
    sums = {}
    for line in text.splitlines():
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if match:
            sums[match.group(2)] = match.group(1).lower()
    return sums


def install_kind(executable: str | None = None, frozen: bool | None = None,
                 environ: dict | None = None, platform: str | None = None) -> str:
    """Cómo se instaló BlackPDF, para saber cómo actualizarlo."""
    executable = executable or sys.executable
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    if environ.get("APPIMAGE"):
        return KIND_APPIMAGE
    if frozen and platform == "win32" and (Path(executable).parent / "unins000.exe").exists():
        return KIND_WINDOWS
    if frozen and platform.startswith("linux") and executable.startswith("/opt/blackpdf/"):
        return KIND_DEB
    return KIND_MANUAL


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={
        "User-Agent": f"BlackPDF/{__version__}",
        "Accept": "application/vnd.github+json" if url.startswith("https://api.github.com/") else "*/*",
    })
    return urllib.request.urlopen(request, timeout=timeout)


def _get(url: str, timeout: float = 15) -> bytes:
    with _open(url, timeout) as response:
        return response.read()


def fetch_latest(progress=None, timeout: float = 15) -> Release | None:
    """Última versión publicada, o None si todavía no hay ninguna."""
    try:
        data = json.loads(_get(API_LATEST, timeout))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateCheckError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateCheckError(str(exc)) from exc
    release = parse_release(data)
    return release if release.version else None


def download(release: Release, kind: str, folder: str, progress=None, timeout: float = 30) -> str:
    """Descarga el archivo de `kind` en `folder` y comprueba su SHA-256. Devuelve la ruta."""
    name = asset_for(release, kind)
    if name is None:
        raise UpdateDownloadError("no hay archivo para este sistema")
    sums_name = next((n for n in CHECKSUMS if n in release.assets), None)
    if sums_name is None:
        raise UpdateVerifyError("la versión no publica sumas SHA-256")
    try:
        expected = parse_checksums(_get(release.assets[sums_name], timeout).decode("utf-8", "replace")).get(name)
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateDownloadError(str(exc)) from exc
    if not expected:
        raise UpdateVerifyError(f"sin suma para {name}")

    target = Path(folder) / name
    partial = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial.unlink(missing_ok=True)
    except OSError as exc:
        raise WriteError(str(exc), path=str(target)) from exc
    digest = hashlib.sha256()
    try:
        with _open(release.assets[name], timeout) as response, open(partial, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None and total:
                    progress(min(done, total), total)
    except PermissionError as exc:
        raise WriteError(str(exc), path=str(target)) from exc
    except (urllib.error.URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateDownloadError(str(exc)) from exc
    if digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise UpdateVerifyError(f"la suma de {name} no coincide")
    os.replace(partial, target)
    return str(target)


def replace_appimage(new_file: str, current: str) -> None:
    """Pone el AppImage nuevo en lugar del actual (el que está abierto sigue funcionando hasta reiniciar)."""
    try:
        os.chmod(new_file, 0o755)
        os.replace(new_file, current)
    except OSError as exc:
        raise WriteError(str(exc), path=current) from exc
