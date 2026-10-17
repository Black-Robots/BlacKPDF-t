"""Tema visual de BlackPDF. Única fuente de colores de la interfaz.

Oscuro grafito por defecto y claro; acento rojo #E5484D. Tipografía Inter,
bordes de 8 px y espaciado en múltiplos de 4.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
FONTS_DIR = RESOURCES / "fonts"

RADIUS = 8
RADIUS_SMALL = 6
SPACE = 4
FONT_FAMILY = "Inter"
FONT_SIZE = 13
ANIMATION_MS = 180


def space(n: int) -> int:
    """Múltiplos de 4 px."""
    return SPACE * n


@dataclass(frozen=True)
class Palette:
    name: str
    is_dark: bool
    window: str
    surface: str
    surface_alt: str
    hover: str
    pressed: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_soft: str
    viewer: str
    page_shadow: str
    selection: str
    search_hit: str
    search_current: str
    success: str
    warning: str
    danger: str
    scrim: str
    night_page: str
    night_ink: str

    def qcolor(self, name: str) -> QColor:
        return parse_color(getattr(self, name))


DARK = Palette(
    name="dark",
    is_dark=True,
    window="#101012",
    surface="#16161A",
    surface_alt="#1D1D22",
    hover="#26262C",
    pressed="#2F2F37",
    border="#2A2A31",
    border_strong="#3B3B44",
    text="#EDEDF0",
    text_secondary="#C6C6CE",
    text_muted="#9A9AA5",
    text_faint="#64646E",
    accent="#E5484D",
    accent_hover="#EC6266",
    accent_pressed="#C93B40",
    accent_text="#FFFFFF",
    accent_soft="rgba(229, 72, 77, 0.16)",
    viewer="#0B0B0D",
    page_shadow="rgba(0, 0, 0, 0.55)",
    selection="rgba(80, 140, 255, 0.32)",
    search_hit="rgba(255, 196, 0, 0.38)",
    search_current="rgba(229, 72, 77, 0.50)",
    success="#3DD68C",
    warning="#F5A524",
    danger="#F2555A",
    scrim="rgba(0, 0, 0, 0.55)",
    night_page="#1B1B1F",
    night_ink="#DADADF",
)

LIGHT = Palette(
    name="light",
    is_dark=False,
    window="#F3F3F5",
    surface="#FFFFFF",
    surface_alt="#F7F7F9",
    hover="#EBEBEF",
    pressed="#E1E1E7",
    border="#E2E2E7",
    border_strong="#CFCFD7",
    text="#17171C",
    text_secondary="#3D3D46",
    text_muted="#6A6A75",
    text_faint="#A1A1AB",
    accent="#E5484D",
    accent_hover="#D63D42",
    accent_pressed="#BF3439",
    accent_text="#FFFFFF",
    accent_soft="rgba(229, 72, 77, 0.10)",
    viewer="#E4E4E8",
    page_shadow="rgba(20, 20, 30, 0.22)",
    selection="rgba(40, 110, 240, 0.25)",
    search_hit="rgba(255, 190, 0, 0.40)",
    search_current="rgba(229, 72, 77, 0.45)",
    success="#16A05A",
    warning="#C27A00",
    danger="#DC3E42",
    scrim="rgba(10, 10, 15, 0.35)",
    night_page="#1B1B1F",
    night_ink="#DADADF",
)

PALETTES = {"dark": DARK, "light": LIGHT}


def parse_color(value: str) -> QColor:
    value = value.strip()
    if value.startswith("rgba"):
        inner = value[value.index("(") + 1 : value.rindex(")")]
        r, g, b, a = (part.strip() for part in inner.split(","))
        color = QColor(int(r), int(g), int(b))
        color.setAlphaF(float(a))
        return color
    return QColor(value)


def load_fonts() -> None:
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def _cache_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    folder = Path(base or Path.home() / ".cache" / "BlackPDF") / "theme"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


_ICON_SVGS = {
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "dot": None,
}


def _write_svg(name: str, color: str, stroke: float = 2.4) -> str:
    folder = _cache_dir()
    safe = color.replace("#", "").replace(" ", "").replace("(", "").replace(")", "").replace(",", "_")
    path = folder / f"{name}-{safe}.svg"
    if not path.exists():
        if name == "dot":
            body = f'<circle cx="12" cy="12" r="5" fill="{color}" stroke="none"/>'
        else:
            body = _ICON_SVGS[name]
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>'
        )
        path.write_text(svg, encoding="utf-8")
    return path.as_posix()


def stylesheet(p: Palette) -> str:
    chevron = _write_svg("chevron-down", p.text_muted)
    chevron_up = _write_svg("chevron-up", p.text_muted)
    check = _write_svg("check", p.accent_text, 3.0)
    dot = _write_svg("dot", p.accent_text)
    r = RADIUS
    return f"""
* {{
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_SIZE}px;
    outline: none;
}}
QWidget {{
    color: {p.text};
    background: transparent;
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
QMainWindow, QDialog, QWidget#Window {{
    background: {p.window};
}}
QDialog {{
    background: {p.surface};
}}
QToolTip {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SMALL}px;
    padding: 4px 8px;
}}

/* ---------- Superficies ---------- */
QFrame#Header, QFrame#DocToolbar, QFrame#PaneBar {{
    background: {p.surface};
    border: none;
    border-bottom: 1px solid {p.border};
}}
QFrame#Sidebar {{
    background: {p.surface};
    border: none;
    border-right: 1px solid {p.border};
}}
QFrame#FloatingBar {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 12px;
}}
QFrame[role="card"] {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {r}px;
}}
QFrame[role="panel"] {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {r}px;
}}
QFrame[role="separator"] {{
    background: {p.border};
    border: none;
}}
QListView#Organizer, QWidget#OrganizerPanel {{ background: {p.viewer}; }}
QListView#Organizer::item, QListView#Organizer::item:hover, QListView#Organizer::item:selected {{ background: transparent; }}
QWidget#HomePage, QScrollArea#HomeScroll, QWidget#HomeContent {{
    background: {p.window};
}}

/* ---------- Texto ---------- */
QLabel {{ background: transparent; }}
QLabel[role="hero"] {{ font-size: 30px; font-weight: 700; }}
QLabel[role="title"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="subtitle"] {{ font-size: 14px; color: {p.text_muted}; }}
QLabel[role="body"] {{ color: {p.text_secondary}; }}
QLabel[role="muted"] {{ color: {p.text_muted}; }}
QLabel[role="caption"] {{ font-size: 12px; color: {p.text_muted}; }}
QLabel[role="section"] {{ font-size: 11px; font-weight: 600; color: {p.text_muted}; }}
QLabel[role="cardtitle"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="brand"] {{ font-size: 15px; font-weight: 700; }}
QLabel[role="error"] {{ color: {p.danger}; font-size: 12px; }}
QLabel[role="success"] {{ color: {p.success}; }}
QLabel[role="kbd"] {{
    font-size: 11px; color: {p.text_muted};
    background: {p.surface_alt}; border: 1px solid {p.border};
    border-radius: 4px; padding: 1px 6px;
}}

/* ---------- Botones ---------- */
QPushButton {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {r}px;
    padding: 7px 16px;
    font-weight: 500;
    min-height: 18px;
}}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:pressed {{ background: {p.pressed}; }}
QPushButton:disabled {{ color: {p.text_faint}; border-color: {p.border}; }}
QPushButton:focus {{ border-color: {p.accent}; }}
QPushButton[variant="primary"] {{
    background: {p.accent}; color: {p.accent_text}; border: 1px solid {p.accent};
}}
QPushButton[variant="primary"]:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {p.accent_pressed}; }}
QPushButton[variant="primary"]:disabled {{
    background: {p.hover}; color: {p.text_faint}; border-color: {p.hover};
}}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {p.hover}; }}
QPushButton[variant="danger"] {{ color: {p.danger}; }}
QPushButton[variant="link"] {{
    background: transparent; border: none; color: {p.accent}; padding: 0; min-height: 0;
}}
QPushButton[variant="link"]:hover {{ color: {p.accent_hover}; }}
QPushButton[variant="large"] {{ padding: 10px 22px; font-size: 14px; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r}px;
    padding: 5px;
    color: {p.text_secondary};
}}
QToolButton:hover {{ background: {p.hover}; color: {p.text}; }}
QToolButton:pressed {{ background: {p.pressed}; }}
QToolButton:checked {{ background: {p.accent_soft}; color: {p.accent}; }}
QToolButton:disabled {{ color: {p.text_faint}; }}
QToolButton[variant="text"] {{ padding: 5px 10px; font-weight: 500; }}
QToolButton[variant="segment"] {{
    padding: 5px 12px; font-weight: 500; color: {p.text_muted}; border-radius: {RADIUS_SMALL}px;
}}
QToolButton[variant="segment"]:checked {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
}}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QFrame[role="segmented"] {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {r}px;
}}

/* ---------- Campos ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {r}px;
    padding: 6px 10px;
    selection-background-color: {p.accent};
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: {p.text_faint};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QComboBox:on {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {p.text_faint}; }}
QLineEdit[invalid="true"] {{ border-color: {p.danger}; }}
QLineEdit[variant="search"] {{ padding-left: 32px; }}
QLineEdit[variant="bare"] {{ background: transparent; border: none; padding: 2px 4px; }}
QComboBox {{ padding-right: 28px; }}
QComboBox#ZoomBox {{ padding: 4px 24px 4px 6px; }}
QComboBox QLineEdit {{ background: transparent; border: none; padding: 0; margin: 0; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: {r}px;
    padding: 4px;
    selection-background-color: {p.hover};
    selection-color: {p.text};
}}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; border-radius: {RADIUS_SMALL}px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; border: none; background: transparent; margin: 3px 3px 0 0;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; border: none; background: transparent; margin: 0 3px 3px 0;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{chevron_up}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 10px; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {p.border_strong};
    background: {p.surface_alt};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: url("{check}"); }}
QRadioButton::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: url("{dot}"); }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p.text_faint}; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.hover}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0;
    border-radius: 8px; background: {p.text}; border: 3px solid {p.accent};
}}
QSlider::handle:horizontal:hover {{ background: {p.accent_text}; }}

QProgressBar {{
    background: {p.hover}; border: none; border-radius: 3px;
    height: 6px; max-height: 6px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}

/* ---------- Listas ---------- */
QListView, QTreeView, QListWidget, QTreeWidget, QTableView {{
    background: transparent;
    border: none;
    show-decoration-selected: 1;
}}
QListView::item, QTreeView::item {{ border-radius: {RADIUS_SMALL}px; padding: 4px 6px; }}
QListView::item:hover, QTreeView::item:hover {{ background: {p.hover}; }}
QListView::item:selected, QTreeView::item:selected {{ background: {p.accent_soft}; color: {p.text}; }}
QListWidget[role="filelist"] {{
    background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: {r}px; padding: 4px;
}}
QListWidget[role="filelist"]::item {{ padding: 8px; margin: 1px 0; }}
QTreeView::branch {{ background: transparent; }}
QHeaderView::section {{
    background: transparent; color: {p.text_muted}; border: none;
    border-bottom: 1px solid {p.border}; padding: 6px;
}}

/* ---------- Barras de desplazamiento ---------- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 4px; min-height: 32px; margin: 0 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 4px; min-width: 32px; margin: 2px 0; }}
QScrollBar::handle:hover {{ background: {p.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: transparent; }}

/* ---------- Pestañas ---------- */
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {p.text_muted};
    border: 1px solid transparent;
    border-radius: {r}px;
    padding: 0px 6px 0px 10px;
    margin: 0px 2px;
    height: 34px;
    min-width: 40px;
    max-width: 220px;
}}
QTabBar::tab:hover {{ background: {p.hover}; color: {p.text}; }}
QTabBar::tab:selected {{ background: {p.surface_alt}; color: {p.text}; border-color: {p.border_strong}; }}
QTabBar QToolButton {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS_SMALL}px; }}

/* ---------- Menús ---------- */
QMenu {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: {r}px;
    padding: 4px;
}}
QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: {RADIUS_SMALL}px; background: transparent; }}
QMenu::item:selected {{ background: {p.hover}; }}
QMenu::item:disabled {{ color: {p.text_faint}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 6px; }}
QMenu::right-arrow {{ width: 10px; height: 10px; }}

QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QStatusBar {{ background: {p.surface}; border-top: 1px solid {p.border}; color: {p.text_muted}; }}
QMessageBox {{ background: {p.surface}; }}
QMessageBox QLabel {{ color: {p.text}; }}
"""


def qt_palette(p: Palette) -> QPalette:
    pal = QPalette()
    role = QPalette.ColorRole
    pal.setColor(role.Window, parse_color(p.window))
    pal.setColor(role.WindowText, parse_color(p.text))
    pal.setColor(role.Base, parse_color(p.surface_alt))
    pal.setColor(role.AlternateBase, parse_color(p.surface))
    pal.setColor(role.Text, parse_color(p.text))
    pal.setColor(role.Button, parse_color(p.surface_alt))
    pal.setColor(role.ButtonText, parse_color(p.text))
    pal.setColor(role.Highlight, parse_color(p.accent))
    pal.setColor(role.HighlightedText, parse_color(p.accent_text))
    pal.setColor(role.ToolTipBase, parse_color(p.surface_alt))
    pal.setColor(role.ToolTipText, parse_color(p.text))
    pal.setColor(role.PlaceholderText, parse_color(p.text_faint))
    pal.setColor(role.Link, parse_color(p.accent))
    pal.setColor(role.Mid, parse_color(p.border))
    pal.setColor(role.Dark, parse_color(p.border_strong))
    pal.setColor(role.Light, parse_color(p.hover))
    pal.setColor(QPalette.ColorGroup.Disabled, role.Text, parse_color(p.text_faint))
    pal.setColor(QPalette.ColorGroup.Disabled, role.WindowText, parse_color(p.text_faint))
    pal.setColor(QPalette.ColorGroup.Disabled, role.ButtonText, parse_color(p.text_faint))
    return pal


class ThemeManager(QObject):
    """Mantiene el tema activo y avisa cuando cambia."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self._mode = "dark"
        self._palette = DARK

    @property
    def mode(self) -> str:
        """"dark", "light" o "system"."""
        return self._mode

    @property
    def palette(self) -> Palette:
        return self._palette

    def resolve(self, mode: str) -> Palette:
        if mode == "system":
            hints = QGuiApplication.styleHints()
            scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Dark
            return LIGHT if scheme == Qt.ColorScheme.Light else DARK
        return PALETTES.get(mode, DARK)

    def apply(self, mode: str) -> None:
        self._mode = mode if mode in ("dark", "light", "system") else "dark"
        self._palette = self.resolve(self._mode)
        app = QApplication.instance()
        if app is not None:
            font = QFont(FONT_FAMILY)
            font.setPixelSize(FONT_SIZE)
            font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            app.setFont(font)
            app.setPalette(qt_palette(self._palette))
            app.setStyleSheet(stylesheet(self._palette))
        self.changed.emit()

    def toggle(self) -> None:
        self.apply("light" if self._palette.is_dark else "dark")


_manager: ThemeManager | None = None


def manager() -> ThemeManager:
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def current() -> Palette:
    return manager().palette


def color(name: str) -> QColor:
    return current().qcolor(name)


def palette_fields() -> list[str]:
    return [f.name for f in fields(Palette)]
