"""GUI entry point."""

from __future__ import annotations

import os
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME
    from . import theme
    from .common import app_icon
    from .main_window import MainWindow

    if sys.platform == "win32":
        try:   # own taskbar icon instead of the Python one
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CodeScope.CodeScope")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication([sys.argv[0]] + argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("CodeScope")
    app.setWindowIcon(app_icon())
    settings = QSettings("CodeScope", "CodeScope")
    theme.apply_theme(app, settings.value("theme", "light"))
    win = MainWindow()
    win.show()
    for a in argv:
        if a.endswith(".csdb") and os.path.exists(a):
            win.open_project(a)
            break
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
