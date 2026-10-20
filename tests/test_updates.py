"""Comprobación y descarga de versiones nuevas (sin internet: archivos locales y respuestas simuladas)."""

from __future__ import annotations

import hashlib
import json
import urllib.error

import pytest

from blackpdf.core import updates
from blackpdf.core.errors import UpdateCheckError, UpdateDownloadError, UpdateVerifyError


def test_versions_compare_numerically():
    assert updates.parse_version("v1.2.10") == (1, 2, 10)
    assert updates.parse_version("2.0.0-beta.1") == (2, 0, 0)
    assert updates.is_newer("1.0.1", "1.0.0")
    assert updates.is_newer("v1.10.0", "1.9.9")
    assert updates.is_newer("2", "1.9")
    assert not updates.is_newer("1.0", "1.0.0")
    assert not updates.is_newer("0.9.9", "1.0.0")


def _api_release(assets: dict[str, str]) -> dict:
    return {
        "tag_name": "v1.1.0",
        "html_url": "https://github.com/Black-Robots/BlackPDF/releases/tag/v1.1.0",
        "body": "### Novedades\n- Algo nuevo",
        "published_at": "2026-11-01T10:00:00Z",
        "assets": [{"name": name, "browser_download_url": url} for name, url in assets.items()],
    }


def test_parse_release_and_pick_assets():
    release = updates.parse_release(_api_release({
        "BlackPDF-1.1.0-Setup.exe": "u1",
        "BlackPDF-1.1.0-x86_64.AppImage": "u2",
        "blackpdf_1.1.0_amd64.deb": "u3",
        "SHA256SUMS.txt": "u4",
    }))
    assert release.version == "1.1.0" and release.notes.startswith("### Novedades")
    assert updates.asset_for(release, updates.KIND_WINDOWS) == "BlackPDF-1.1.0-Setup.exe"
    assert updates.asset_for(release, updates.KIND_APPIMAGE) == "BlackPDF-1.1.0-x86_64.AppImage"
    assert updates.asset_for(release, updates.KIND_DEB) == "blackpdf_1.1.0_amd64.deb"
    assert updates.asset_for(release, updates.KIND_MANUAL) is None


def test_parse_checksums_formats():
    a, b = "a" * 64, "B" * 64
    sums = updates.parse_checksums(f"{a}  BlackPDF-1.1.0-Setup.exe\n{b} *blackpdf.deb\nbasura\n")
    assert sums == {"BlackPDF-1.1.0-Setup.exe": a, "blackpdf.deb": b.lower()}


def test_install_kind_detection(tmp_path):
    exe = tmp_path / "BlackPDF.exe"
    exe.write_bytes(b"")
    assert updates.install_kind(str(exe), True, {}, "win32") == updates.KIND_MANUAL, "portable: sin desinstalador"
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert updates.install_kind(str(exe), True, {}, "win32") == updates.KIND_WINDOWS
    assert updates.install_kind("/opt/blackpdf/blackpdf", True, {}, "linux") == updates.KIND_DEB
    assert updates.install_kind("/usr/bin/python3", True, {"APPIMAGE": "/home/x/B.AppImage"}, "linux") == updates.KIND_APPIMAGE
    assert updates.install_kind("/usr/bin/python3", False, {}, "linux") == updates.KIND_MANUAL


def test_fetch_latest_handles_answers(monkeypatch):
    monkeypatch.setattr(updates, "_get", lambda url, timeout=15: json.dumps(_api_release({})).encode())
    assert updates.fetch_latest().version == "1.1.0"

    def not_found(url, timeout=15):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(updates, "_get", not_found)
    assert updates.fetch_latest() is None, "todavía no hay versiones publicadas"

    def offline(url, timeout=15):
        raise urllib.error.URLError("sin conexión")

    monkeypatch.setattr(updates, "_get", offline)
    with pytest.raises(UpdateCheckError):
        updates.fetch_latest()


def _local_release(tmp_path, payload: bytes, checksum: str | None) -> updates.Release:
    source = tmp_path / "publicado"
    source.mkdir()
    setup = source / "BlackPDF-1.1.0-Setup.exe"
    setup.write_bytes(payload)
    assets = {setup.name: setup.as_uri()}
    if checksum is not None:
        sums = source / "SHA256SUMS.txt"
        sums.write_text(f"{checksum}  {setup.name}\n")
        assets[sums.name] = sums.as_uri()
    return updates.Release("1.1.0", "https://example.org", assets=assets)


def test_download_verifies_checksum(tmp_path):
    payload = b"instalador" * 100_000
    release = _local_release(tmp_path, payload, hashlib.sha256(payload).hexdigest())
    steps = []
    path = updates.download(release, updates.KIND_WINDOWS, str(tmp_path / "descargas"),
                            progress=lambda done, total: steps.append((done, total)))
    assert open(path, "rb").read() == payload
    assert steps and steps[-1] == (len(payload), len(payload))
    assert not list((tmp_path / "descargas").glob("*.part"))


def test_download_rejects_wrong_or_missing_checksum(tmp_path):
    release = _local_release(tmp_path, b"manipulado", "0" * 64)
    with pytest.raises(UpdateVerifyError):
        updates.download(release, updates.KIND_WINDOWS, str(tmp_path / "d1"))
    assert not list((tmp_path / "d1").iterdir()), "no queda nada de una descarga que no coincide"

    (tmp_path / "otra").mkdir()
    unsigned = _local_release(tmp_path / "otra", b"x", None)
    with pytest.raises(UpdateVerifyError):
        updates.download(unsigned, updates.KIND_WINDOWS, str(tmp_path / "d2"))
    with pytest.raises(UpdateDownloadError):
        updates.download(release, updates.KIND_DEB, str(tmp_path / "d3"))


def test_replace_appimage(tmp_path):
    current = tmp_path / "BlackPDF.AppImage"
    current.write_bytes(b"viejo")
    new = tmp_path / "BlackPDF-1.1.0-x86_64.AppImage"
    new.write_bytes(b"nuevo")
    updates.replace_appimage(str(new), str(current))
    assert current.read_bytes() == b"nuevo" and not new.exists()
    assert current.stat().st_mode & 0o111
