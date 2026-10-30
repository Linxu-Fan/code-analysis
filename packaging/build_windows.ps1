# One-click Windows build for CodeScope (started by build.bat in the repository root).
#
# Produces in dist\:
#   CodeScope\                       portable application folder (CodeScope.exe, codescope-cli.exe)
#   CodeScope-<ver>-portable.zip     zipped portable folder
#   CodeScope-<ver>-Setup.exe        installer (needs Inno Setup 6; offered for install via winget)
#
# Options:
#   -SkipTests      do not run the test suite before building
#   -SkipInstaller  do not build the Setup.exe installer
#   -Clean          delete the build environment and previous output first
#   -Yes            answer "yes" to install prompts (Python / Inno Setup via winget)
#   -NoPause        do not wait for a key press at the end (for scripts and CI)

param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$Clean,
    [switch]$Yes,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$Venv = Join-Path $Root ".build-venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$Dist = Join-Path $Root "dist"
# PyInstaller's intermediate files (including non-runnable .exe stubs) go to a hidden folder
# that is removed after a successful build, so only dist\ contains executables.
$Work = Join-Path $Root ".build-work"
$Started = Get-Date

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Write-Note($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Finish($code) {
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to close" | Out-Null
    }
    exit $code
}

function Fail($msg) {
    Write-Host ""
    Write-Host "BUILD FAILED: $msg" -ForegroundColor Red
    Finish 1
}

function Confirm-Action($question) {
    if ($Yes) { return $true }
    if ($NoPause) { return $false }
    $answer = Read-Host "$question [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

# Run a native command and stop the build if it fails.
function Invoke-Checked($description, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) { Fail "$description (exit code $LASTEXITCODE)" }
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

# ---- Python -----------------------------------------------------------------------------------
# Returns @{Exe=...; Args=@(...)} for a Python >= 3.10, or $null.
function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $check = "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
            & $c.Exe @($c.Args) -c $check 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch { }
    }
    return $null
}

Write-Host "CodeScope Windows build" -ForegroundColor White
Write-Host "Repository: $Root"

if ($Clean) {
    Write-Step "Cleaning previous build"
    foreach ($p in @($Venv, $Work, (Join-Path $Root "build"), $Dist)) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Ok "removed $p" }
    }
}

Write-Step "Looking for Python 3.10 or newer"
$py = Find-Python
if (-not $py) {
    Write-Note "Python 3.10+ was not found."
    if ((Test-Winget) -and (Confirm-Action "Install Python 3.12 now with winget?")) {
        Invoke-Checked "Installing Python" {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        }
        Update-SessionPath
        $py = Find-Python
    }
    if (-not $py) {
        Fail "Python 3.10+ is required. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run build.bat again."
    }
}
$pyVersion = & $py.Exe @($py.Args) -c "import sys; print(sys.version.split()[0])"
Write-Ok "using $($py.Exe) $($py.Args -join ' ') (Python $pyVersion)"

# ---- build environment ------------------------------------------------------------------------
Write-Step "Preparing the build environment (.build-venv)"
if (-not (Test-Path $VenvPython)) {
    Invoke-Checked "Creating the virtual environment" { & $py.Exe @($py.Args) -m venv $Venv }
    Write-Ok "created $Venv"
} else {
    Write-Ok "reusing $Venv"
}
Invoke-Checked "Upgrading pip" { & $VenvPython -m pip install --disable-pip-version-check -q --upgrade pip }
Write-Step "Installing dependencies (first run downloads about 100 MB)"
Invoke-Checked "Installing dependencies" {
    & $VenvPython -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller pytest
}
Write-Ok "dependencies installed"

$Version = (& $VenvPython -c "import codescope; print(codescope.__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) { Fail "Could not read the CodeScope version" }
Write-Ok "CodeScope version $Version"

# ---- tests -------------------------------------------------------------------------------------
if (-not $SkipTests) {
    Write-Step "Running the test suite"
    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-Checked "Tests failed" { & $VenvPython -m pytest -q }
    Remove-Item Env:QT_QPA_PLATFORM
    Write-Ok "all tests passed"
} else {
    Write-Note "tests skipped (-SkipTests)"
}

# ---- executables -------------------------------------------------------------------------------
Write-Step "Building executables with PyInstaller (takes a minute or two)"
$AppDir = Join-Path $Dist "CodeScope"
$OldWork = Join-Path $Root "build\codescope"
if (Test-Path $OldWork) {
    # left over by earlier builds; its CodeScope.exe cannot run and only causes confusion
    Remove-Item -Recurse -Force $OldWork -ErrorAction SilentlyContinue
    $OldBuild = Join-Path $Root "build"
    if ((Test-Path $OldBuild) -and -not (Get-ChildItem $OldBuild -Force)) { Remove-Item -Force $OldBuild }
}
if (Test-Path $AppDir) {
    try { Remove-Item -Recurse -Force $AppDir }
    catch { Fail "Cannot replace $AppDir. Close any running CodeScope.exe and try again." }
}
Invoke-Checked "PyInstaller build" {
    & $VenvPython -m PyInstaller --noconfirm --clean --log-level WARN --distpath $Dist --workpath $Work packaging\codescope.spec
}
$Cli = Join-Path $AppDir "codescope-cli.exe"
if (-not (Test-Path (Join-Path $AppDir "CodeScope.exe")) -or -not (Test-Path $Cli)) {
    Fail "PyInstaller finished but the executables are missing in $AppDir"
}
Invoke-Checked "Smoke test of codescope-cli.exe" { & $Cli --version }
# user documentation next to the program (AI > AI Setup Guide opens docs\AI_SETUP.md)
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "docs") | Out-Null
Copy-Item (Join-Path $Root "docs\AI_SETUP.md") (Join-Path $AppDir "docs")
Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
Write-Ok "built $AppDir"

Write-Step "Creating the portable zip"
$Zip = Join-Path $Dist "CodeScope-$Version-portable.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path $AppDir -DestinationPath $Zip
Write-Ok "created $Zip"

# ---- installer ---------------------------------------------------------------------------------
function Find-Iscc {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $paths = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($p in $paths) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}

$Setup = $null
if (-not $SkipInstaller) {
    Write-Step "Building the installer (Inno Setup)"
    $iscc = Find-Iscc
    if (-not $iscc -and (Test-Winget) -and (Confirm-Action "Inno Setup 6 is needed for Setup.exe. Install it now with winget?")) {
        Invoke-Checked "Installing Inno Setup" {
            winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
        }
        Update-SessionPath
        $iscc = Find-Iscc
    }
    if ($iscc) {
        Invoke-Checked "Inno Setup compile" {
            & $iscc /Q "/DAppVersion=$Version" "/DSourceDir=..\dist\CodeScope" packaging\installer.iss
        }
        $Setup = Join-Path $Dist "CodeScope-$Version-Setup.exe"
        Write-Ok "created $Setup"
    } else {
        Write-Note "Inno Setup 6 not found: skipping Setup.exe (get it from https://jrsoftware.org/isdl.php)."
        Write-Note "The portable folder and zip were still built."
    }
} else {
    Write-Note "installer skipped (-SkipInstaller)"
}

# ---- summary -----------------------------------------------------------------------------------
$elapsed = [int]((Get-Date) - $Started).TotalSeconds
Write-Host ""
Write-Host "BUILD SUCCEEDED in $elapsed s" -ForegroundColor Green
Write-Host "  Application : $AppDir\CodeScope.exe   <-- run this one"
Write-Host "  Command line: $Cli"
Write-Host "  Portable zip: $Zip"
if ($Setup) { Write-Host "  Installer   : $Setup" }
if (-not $NoPause) { Start-Process explorer.exe $Dist }
Finish 0
