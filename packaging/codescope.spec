# PyInstaller spec: builds CodeScope.exe (GUI) and codescope-cli.exe (console) into one folder.
#   pyinstaller --noconfirm packaging/codescope.spec
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from codescope import __version__  # noqa: E402

ICON = os.path.join(ROOT, "codescope", "resources", "codescope.ico")
datas = [(os.path.join(ROOT, "codescope", "resources"), "codescope/resources")]
binaries = []
for mod in ("tree_sitter", "tree_sitter_c", "tree_sitter_cpp", "tree_sitter_python"):
    binaries += collect_dynamic_libs(mod)
hidden = ["tree_sitter_c", "tree_sitter_cpp", "tree_sitter_python",
          "codescope.core.extract.python", "codescope.core.extract.cfamily",
          "pygments.lexers.c_cpp", "pygments.lexers.python", "pygments.formatters.html",
          "pygments.styles.default"]
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtBluetooth", "PySide6.QtNetwork",
            "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL", "PySide6.QtQuickWidgets",
            "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
            "numpy", "matplotlib", "PIL", "pytest"]

version_file = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct, VSVersionInfo)
    nums = tuple(int(x) for x in (__version__.split(".") + ["0", "0", "0"])[:4])
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "CodeScope"),
            StringStruct("FileDescription", "CodeScope static code analysis"),
            StringStruct("FileVersion", __version__),
            StringStruct("ProductName", "CodeScope"),
            StringStruct("ProductVersion", __version__),
            StringStruct("OriginalFilename", "CodeScope.exe"),
        ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])


def analysis(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[ROOT], binaries=binaries, datas=datas,
                    hiddenimports=hidden, excludes=excludes, noarchive=False)


# Qt modules pulled in by plugins that CodeScope never uses
DROP = ("qt6qml", "qt6quick", "virtualkeyboard", "qt6pdf", "qt6webengine", "qt6network", "qt6opengl",
        "qt6waylandclient", "qt6wlshell", "qt6eglfs", "/qml/", "\\qml\\", "translations", "/tls/", "\\tls\\",
        "networkinformation", "qtquick", "opengl32sw", "qt6virtual", "qpdf", "qtpdf")


def _keep(entry):
    name = entry[0].replace("\\", "/").lower()
    return not any(d.replace("\\", "/") in name for d in DROP)


gui_a = analysis("gui_entry.py")
cli_a = analysis("cli_entry.py")
for a in (gui_a, cli_a):
    a.binaries = [b for b in a.binaries if _keep(b)]
    a.datas = [d for d in a.datas if _keep(d)]
gui_pyz = PYZ(gui_a.pure)
cli_pyz = PYZ(cli_a.pure)

gui_exe = EXE(gui_pyz, gui_a.scripts, [], exclude_binaries=True, name="CodeScope", console=False,
              icon=ICON, version=version_file, upx=False)
cli_exe = EXE(cli_pyz, cli_a.scripts, [], exclude_binaries=True, name="codescope-cli", console=True,
              icon=ICON, version=version_file, upx=False)

coll = COLLECT(gui_exe, cli_exe,
               gui_a.binaries, gui_a.datas, cli_a.binaries, cli_a.datas,
               strip=False, upx=False, name="CodeScope")
