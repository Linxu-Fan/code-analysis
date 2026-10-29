# Local Windows build: creates dist\CodeScope (portable folder), a zip and the installer.
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
$version = python -c "import codescope; print(codescope.__version__)"
python -m PyInstaller --noconfirm --clean packaging\codescope.spec
& dist\CodeScope\codescope-cli.exe --version
Compress-Archive -Path dist\CodeScope -DestinationPath "dist\CodeScope-$version-portable.zip" -Force
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (Test-Path $iscc) {
    & $iscc "/DAppVersion=$version" "/DSourceDir=..\dist\CodeScope" packaging\installer.iss
} else {
    Write-Warning "Inno Setup not found; skipping installer (choco install innosetup)"
}
Get-ChildItem dist
