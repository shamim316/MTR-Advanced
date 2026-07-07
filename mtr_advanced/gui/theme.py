"""Application theming: dark (default) and light Fusion palettes."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

# Shared status colours (chosen to read on both themes)
COLOR_OK = QColor("#2ecc71")        # no loss
COLOR_WARN = QColor("#f39c12")      # low loss
COLOR_LOSS = QColor("#e74c3c")      # heavy loss
COLOR_SILENT = QColor("#7f8c8d")    # hop never answered
COLOR_ACCENT = QColor("#3498db")

# Row-highlight tints (alpha-blended over the base row colour)
HIGHLIGHT_WARN = QColor(243, 156, 18, 60)
HIGHLIGHT_LOSS = QColor(231, 76, 60, 90)
HIGHLIGHT_LOSS_HEAVY = QColor(231, 76, 60, 150)


def loss_tint(loss_pct: float) -> QColor | None:
    """Background tint for a hop row given its packet loss percentage."""
    if loss_pct <= 0:
        return None
    if loss_pct < 5:
        return HIGHLIGHT_WARN
    if loss_pct < 25:
        return HIGHLIGHT_LOSS
    return HIGHLIGHT_LOSS_HEAVY


def loss_node_color(loss_pct: float, silent: bool = False) -> QColor:
    """Fill colour for a node in the path diagram."""
    if silent:
        return COLOR_SILENT
    if loss_pct <= 0:
        return COLOR_OK
    if loss_pct < 5:
        return COLOR_WARN
    return COLOR_LOSS


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    base = QColor(30, 34, 42)
    alt = QColor(37, 42, 52)
    text = QColor(224, 228, 235)
    p.setColor(QPalette.ColorRole.Window, QColor(24, 27, 34))
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, alt)
    p.setColor(QPalette.ColorRole.ToolTipBase, alt)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, QColor(44, 49, 60))
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, COLOR_ACCENT)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(130, 138, 150))
    disabled = QColor(110, 117, 128)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    app.setPalette(p)


def apply_light_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())
