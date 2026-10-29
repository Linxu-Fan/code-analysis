"""Light and dark themes: design tokens, Qt palette and application style sheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from . import icons

TOKENS = {
    "dark": {
        "bg": "#1e1f22",            # editors, views
        "panel": "#2b2d30",         # docks, tool bars, menus
        "panel_alt": "#26282b",
        "header": "#313337",
        "border": "#3c3f44",
        "fg": "#dfe1e5",
        "muted": "#8c9099",
        "faint": "#5f636b",
        "accent": "#4a8cff",
        "accent_fg": "#ffffff",
        "hover": "#383a3f",
        "selection": "#2e4a78",
        "selection_inactive": "#383b42",
        "input": "#1e1f22",
        "scroll": "#4a4d53",
        "scroll_hover": "#5d6067",
        "warn": "#e5a54b",
        "error": "#f0645c",
        "ok": "#5fb878",
        "card": "#2b2d30",
    },
    "light": {
        "bg": "#ffffff",
        "panel": "#f3f4f6",
        "panel_alt": "#eceef1",
        "header": "#e7e9ec",
        "border": "#d4d7dc",
        "fg": "#1f2328",
        "muted": "#656b76",
        "faint": "#a3a8b1",
        "accent": "#2f6fdb",
        "accent_fg": "#ffffff",
        "hover": "#e3e6ea",
        "selection": "#cfe0ff",
        "selection_inactive": "#e1e5eb",
        "input": "#ffffff",
        "scroll": "#c3c7cd",
        "scroll_hover": "#a9aeb6",
        "warn": "#b36b00",
        "error": "#c62828",
        "ok": "#2e8b57",
        "card": "#ffffff",
    },
}

# Editor colour schemes modelled on Visual Studio 2022 (C++ / Python colours).
CODE_SCHEMES = {
    "vs-light": {
        "label": "Visual Studio Light",
        "background": "#ffffff", "foreground": "#000000", "linenumber_bg": "#ffffff",
        "linenumber_fg": "#2b91af", "linenumber_current": "#0b216f", "current_line": None,
        "current_line_border": "#e4e4e4", "indent_guide": "#d0d0d0", "occurrence": "#e0e0e0",
        "selection": "#add6ff", "mark": "#fff3c4",
        "keyword": "#0000ff", "type": "#2b91af", "function": "#74531f", "string": "#a31515",
        "number": "#000000", "comment": "#008000", "preproc": "#808080", "macro": "#8a1bff",
        "namespace": "#000000", "enumerator": "#2f4f4f", "operator": "#000000", "escape": "#b776fb",
        "doc_comment": "#008000",
    },
    "vs-dark": {
        "label": "Visual Studio Dark",
        "background": "#1e1e1e", "foreground": "#dcdcdc", "linenumber_bg": "#1e1e1e",
        "linenumber_fg": "#2b91af", "linenumber_current": "#c6c6c6", "current_line": None,
        "current_line_border": "#464646", "indent_guide": "#404040", "occurrence": "#343434",
        "selection": "#264f78", "mark": "#3a3a22",
        "keyword": "#569cd6", "type": "#4ec9b0", "function": "#dcdcaa", "string": "#d69d85",
        "number": "#b5cea8", "comment": "#57a64a", "preproc": "#9b9b9b", "macro": "#beb7ff",
        "namespace": "#c8c8c8", "enumerator": "#b8d7a3", "operator": "#b4b4b4", "escape": "#ffd68f",
        "doc_comment": "#57a64a",
    },
}
# kept for compatibility with older settings / callers
CODE_THEMES = {"light": CODE_SCHEMES["vs-light"], "dark": CODE_SCHEMES["vs-dark"]}

current = {"name": "dark", "editor_scheme": "auto"}


def tokens():
    return TOKENS[current["name"]]


def editor_scheme_name():
    name = current.get("editor_scheme", "auto")
    if name not in CODE_SCHEMES:
        name = "vs-dark" if current["name"] == "dark" else "vs-light"
    return name


def code_theme():
    return CODE_SCHEMES[editor_scheme_name()]


def is_dark():
    return current["name"] == "dark"


def icon(name):
    """Theme coloured toolbar / menu icon."""
    t = tokens()
    return icons.icon(name, t["fg"], t["accent"], t["faint"])


def _palette(t):
    p = QPalette()
    c = QColor
    p.setColor(QPalette.Window, c(t["panel"]))
    p.setColor(QPalette.WindowText, c(t["fg"]))
    p.setColor(QPalette.Base, c(t["bg"]))
    p.setColor(QPalette.AlternateBase, c(t["panel_alt"]))
    p.setColor(QPalette.ToolTipBase, c(t["panel"]))
    p.setColor(QPalette.ToolTipText, c(t["fg"]))
    p.setColor(QPalette.Text, c(t["fg"]))
    p.setColor(QPalette.Button, c(t["panel"]))
    p.setColor(QPalette.ButtonText, c(t["fg"]))
    p.setColor(QPalette.BrightText, c(t["error"]))
    p.setColor(QPalette.Highlight, c(t["selection"]))
    p.setColor(QPalette.HighlightedText, c(t["fg"]))
    p.setColor(QPalette.Link, c(t["accent"]))
    p.setColor(QPalette.LinkVisited, c(t["accent"]))
    p.setColor(QPalette.PlaceholderText, c(t["faint"]))
    p.setColor(QPalette.Mid, c(t["border"]))
    p.setColor(QPalette.Dark, c(t["border"]))
    p.setColor(QPalette.Light, c(t["header"]))
    p.setColor(QPalette.Midlight, c(t["header"]))
    p.setColor(QPalette.Shadow, c("#000000"))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, c(t["faint"]))
    p.setColor(QPalette.Inactive, QPalette.Highlight, c(t["selection_inactive"]))
    return p


def style_sheet(t):
    down = icons.export_png("chevron_down", t["muted"], 16)
    up = icons.export_png("chevron_up", t["muted"], 16)
    close = icons.export_png("close", t["muted"], 16)
    close_hover = icons.export_png("close", t["fg"], 16)
    tick = icons.export_png("tick", t["accent_fg"], 16)
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {t['panel']}; }}
QMainWindow::separator {{ background: {t['border']}; width: 1px; height: 1px; }}
QToolTip {{ background: {t['panel']}; color: {t['fg']}; border: 1px solid {t['border']}; padding: 4px 6px; }}

/* menus */
QMenuBar {{ background: {t['panel']}; color: {t['fg']}; border-bottom: 1px solid {t['border']}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['hover']}; }}
QMenu {{ background: {t['panel']}; color: {t['fg']}; border: 1px solid {t['border']}; padding: 4px; }}
QMenu::item {{ padding: 5px 28px 5px 26px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t['selection']}; }}
QMenu::item:disabled {{ color: {t['faint']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 6px; }}

/* tool bars */
QToolBar {{ background: {t['panel']}; border: 0; border-bottom: 1px solid {t['border']}; padding: 3px 6px; spacing: 2px; }}
QToolBar::separator {{ background: {t['border']}; width: 1px; margin: 5px 6px; }}
QToolButton {{ background: transparent; color: {t['fg']}; border: 1px solid transparent; border-radius: 5px; padding: 4px; }}
QToolButton:hover {{ background: {t['hover']}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t['selection']}; }}
QToolButton[popupMode="1"] {{ padding-right: 14px; }}

/* docks */
QDockWidget {{ color: {t['muted']}; titlebar-close-icon: url({close}); titlebar-normal-icon: url({up}); }}
QDockWidget::title {{ background: {t['panel']}; padding: 6px 8px; border-bottom: 1px solid {t['border']};
    text-align: left; }}
QDockWidget::close-button, QDockWidget::float-button {{ border: 0; padding: 1px; background: transparent; }}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {t['hover']}; border-radius: 3px; }}

/* tabs */
QTabWidget::pane {{ border: 0; border-top: 1px solid {t['border']}; background: {t['bg']}; }}
QTabBar {{ background: {t['panel']}; qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {t['muted']}; padding: 7px 12px 7px 12px; border: 0;
    border-bottom: 2px solid transparent; margin-right: 1px; }}
QTabBar::tab:hover {{ color: {t['fg']}; background: {t['hover']}; }}
QTabBar::tab:selected {{ color: {t['fg']}; border-bottom: 2px solid {t['accent']}; background: {t['bg']}; }}
QTabBar::close-button {{ image: url({close}); subcontrol-position: right; border-radius: 3px; margin: 2px; }}
QTabBar::close-button:hover {{ image: url({close_hover}); background: {t['hover']}; }}
QTabBar::scroller {{ width: 22px; }}
QTabBar QToolButton {{ background: {t['panel']}; border: 0; }}

/* item views */
QTreeView, QListView, QTableView, QTreeWidget, QListWidget, QTableWidget {{
    background: {t['bg']}; alternate-background-color: {t['panel_alt']}; color: {t['fg']};
    border: 0; selection-background-color: {t['selection']}; selection-color: {t['fg']}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; border: 0; }}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{ background: {t['hover']}; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {t['selection']}; color: {t['fg']}; }}
QTableView {{ gridline-color: {t['border']}; }}
QHeaderView {{ background: {t['panel']}; border: 0; }}
QHeaderView::section {{ background: {t['panel']}; color: {t['muted']}; border: 0; border-right: 1px solid {t['border']};
    border-bottom: 1px solid {t['border']}; padding: 5px 8px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {t['fg']}; }}
QTableCornerButton::section {{ background: {t['panel']}; border: 0; }}

/* inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t['input']}; color: {t['fg']}; border: 1px solid {t['border']}; border-radius: 5px;
    padding: 4px 7px; selection-background-color: {t['selection']}; selection-color: {t['fg']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QComboBox:on {{ border: 1px solid {t['accent']}; }}
QComboBox {{ padding-right: 24px; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {t['faint']}; }}
QComboBox::drop-down {{ border: 0; width: 22px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {t['panel']}; color: {t['fg']}; border: 1px solid {t['border']};
    selection-background-color: {t['selection']}; padding: 2px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; width: 16px; border: 0; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; width: 16px; border: 0; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {t['hover']}; }}

QPushButton {{ background: {t['header']}; color: {t['fg']}; border: 1px solid {t['border']}; border-radius: 5px;
    padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {t['hover']}; border-color: {t['faint']}; }}
QPushButton:pressed {{ background: {t['selection']}; }}
QPushButton:default, QPushButton[primary="true"] {{ background: {t['accent']}; color: {t['accent_fg']}; border-color: {t['accent']}; }}
QPushButton:default:hover, QPushButton[primary="true"]:hover {{ background: {t['accent']}; border-color: {t['fg']}; }}
QPushButton:disabled {{ color: {t['faint']}; background: {t['panel']}; }}
QPushButton:flat {{ background: transparent; border: 0; }}
QPushButton:flat:hover {{ background: {t['hover']}; }}

QCheckBox, QRadioButton {{ color: {t['fg']}; spacing: 6px; background: transparent; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t['faint']}; border-radius: 3px; background: {t['input']}; }}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({tick}); }}
QTreeView::indicator, QTreeWidget::indicator {{ width: 14px; height: 14px; border: 1px solid {t['faint']}; border-radius: 3px; background: {t['input']}; }}
QTreeView::indicator:checked, QTreeWidget::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({tick}); }}
QTreeView::indicator:indeterminate {{ background: {t['faint']}; border-color: {t['faint']}; }}

QGroupBox {{ color: {t['muted']}; border: 1px solid {t['border']}; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QLabel {{ background: transparent; }}

/* scroll bars */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle {{ background: {t['scroll']}; border-radius: 4px; border: 2px solid transparent; }}
QScrollBar::handle:vertical {{ min-height: 28px; margin: 2px 2px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; margin: 2px 2px; }}
QScrollBar::handle:hover {{ background: {t['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

QSplitter::handle {{ background: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QStatusBar {{ background: {t['panel']}; color: {t['muted']}; border-top: 1px solid {t['border']}; }}
QStatusBar::item {{ border: 0; }}
QStatusBar QLabel {{ color: {t['muted']}; padding: 0 6px; }}
QProgressBar {{ background: {t['bg']}; border: 1px solid {t['border']}; border-radius: 4px; height: 10px; text-align: center;
    color: {t['muted']}; font-size: 10px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
QTextBrowser {{ background: {t['bg']}; border: 0; }}

/* custom widgets */
QWidget#PanelBar {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; }}
QLabel#PanelTitle {{ color: {t['fg']}; font-weight: 600; }}
QLabel#Muted {{ color: {t['muted']}; }}
QLabel#Note {{ color: {t['warn']}; }}
QFrame#Card {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; }}
QLabel#CardValue {{ color: {t['fg']}; font-size: 20px; font-weight: 600; }}
QLabel#CardLabel {{ color: {t['muted']}; }}
QLabel#Hero {{ color: {t['fg']}; font-size: 26px; font-weight: 600; }}
QLabel#ChartTitle {{ color: {t['fg']}; font-size: 14px; font-weight: 600; }}
QLabel#Section {{ color: {t['muted']}; font-weight: 600; letter-spacing: 1px; }}
QWidget#Page {{ background: {t['bg']}; }}
QPushButton#Tile {{ text-align: left; padding: 14px 16px; border-radius: 8px; background: {t['card']}; font-size: 13px; }}
QPushButton#Tile:hover {{ border-color: {t['accent']}; }}
QListWidget#Recent::item {{ padding: 8px 6px; border-radius: 5px; }}
"""


def apply_theme(app: QApplication, name: str):
    if name not in TOKENS:
        name = "dark"
    current["name"] = name
    app.setStyle(QStyleFactory.create("Fusion"))
    t = tokens()
    app.setPalette(_palette(t))
    f = QFont(app.font())
    for fam in ("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"):
        if fam in __import__("PySide6.QtGui", fromlist=["QFontDatabase"]).QFontDatabase.families():
            f.setFamily(fam)
            break
    f.setPointSizeF(9.5 if f.pointSizeF() <= 10 else f.pointSizeF())
    app.setFont(f)
    app.setStyleSheet(style_sheet(t))
