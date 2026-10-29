"""Light and dark themes."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

CODE_THEMES = {
    "light": {
        "background": "#ffffff", "foreground": "#1f2328", "linenumber_bg": "#f6f8fa",
        "linenumber_fg": "#8c959f", "current_line": "#fff8dc", "occurrence": "#cfe8ff",
        "selection": "#b6d7ff",
        "Keyword": "#0b51c5", "Keyword.Type": "#6f42c1", "Name.Builtin": "#6f42c1",
        "Name.Function": "#7a3e00", "Name.Class": "#7a3e00", "Name.Decorator": "#8250df",
        "String": "#0a7f3f", "Number": "#b35900", "Comment": "#6e7781", "Comment.Preproc": "#a0337a",
        "Operator": "#1f2328", "Name.Namespace": "#7a3e00",
    },
    "dark": {
        "background": "#1e1f22", "foreground": "#d4d7dd", "linenumber_bg": "#232428",
        "linenumber_fg": "#6c7079", "current_line": "#2a2c31", "occurrence": "#2f4a66",
        "selection": "#214283",
        "Keyword": "#cc7832", "Keyword.Type": "#c58af9", "Name.Builtin": "#c58af9",
        "Name.Function": "#ffc66d", "Name.Class": "#ffc66d", "Name.Decorator": "#bbb529",
        "String": "#6aab73", "Number": "#2aacb8", "Comment": "#7a7e85", "Comment.Preproc": "#bbb529",
        "Operator": "#d4d7dd", "Name.Namespace": "#ffc66d",
    },
}

current = {"name": "light"}


def code_theme():
    return CODE_THEMES[current["name"]]


def apply_theme(app: QApplication, name: str):
    current["name"] = name
    app.setStyle(QStyleFactory.create("Fusion"))
    if name == "dark":
        p = QPalette()
        base, alt, text = QColor("#1e1f22"), QColor("#2b2d30"), QColor("#dfe1e5")
        p.setColor(QPalette.Window, alt)
        p.setColor(QPalette.WindowText, text)
        p.setColor(QPalette.Base, base)
        p.setColor(QPalette.AlternateBase, QColor("#26282c"))
        p.setColor(QPalette.ToolTipBase, alt)
        p.setColor(QPalette.ToolTipText, text)
        p.setColor(QPalette.Text, text)
        p.setColor(QPalette.Button, alt)
        p.setColor(QPalette.ButtonText, text)
        p.setColor(QPalette.BrightText, QColor("#ff6b6b"))
        p.setColor(QPalette.Highlight, QColor("#2f65ca"))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.Link, QColor("#589df6"))
        p.setColor(QPalette.PlaceholderText, QColor("#7a7e85"))
        for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
            p.setColor(QPalette.Disabled, role, QColor("#6c7079"))
        app.setPalette(p)
    else:
        app.setPalette(app.style().standardPalette())
