# Compila BlackPDF en Windows y genera el instalador.
# Requisitos: Python 3.12 o superior (64 bits), CMake 3.24+ con Visual Studio (C) e Inno Setup 6
# (ISCC.exe en el PATH o en su ruta habitual).
# Uso, desde la raíz del proyecto:  powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

$Version = (Select-String -Path "blackpdf\__init__.py" -Pattern '__version__ = "(.+?)"').Matches[0].Groups[1].Value
Write-Host "==> BlackPDF $Version"

if (-not (Test-Path ".venv-win")) {
    python -m venv .venv-win
}
$Py = ".venv-win\Scripts\python.exe"
& $Py -m pip install --disable-pip-version-check -q --upgrade pip
& $Py -m pip install --disable-pip-version-check -q PySide6-Essentials pymupdf pyhanko pyinstaller

Write-Host "==> Biblioteca nativa PRC"
& $Py native\prc\build.py
if ($LASTEXITCODE -ne 0) { throw "No se pudo compilar la biblioteca nativa PRC" }

Write-Host "==> PyInstaller"
& $Py -m PyInstaller --noconfirm --clean --distpath build\dist-win --workpath build\work-win packaging\blackpdf.spec

Write-Host "==> Autocomprobación"
& build\dist-win\BlackPDF\BlackPDF.exe --self-test=build\selftest-win.txt | Out-Null
Get-Content build\selftest-win.txt
if (-not (Select-String -Path build\selftest-win.txt -Pattern "RESULTADO: OK" -Quiet)) {
    throw "La autocomprobación ha fallado"
}

Write-Host "==> Instalador"
$Iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
    foreach ($candidate in @("$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $candidate) { $Iscc = $candidate; break }
    }
}
if (-not $Iscc) { throw "No se encuentra Inno Setup (ISCC.exe)" }
Push-Location packaging\windows
& $Iscc /Q "/DMyAppVersion=$Version" BlackPDF.iss
Pop-Location
Get-ChildItem packaging\out
