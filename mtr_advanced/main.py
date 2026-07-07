"""Application entry point: ``python -m mtr_advanced`` or the packaged EXE."""

from __future__ import annotations

import argparse
import os
import sys


def _asset_path(name: str) -> str:
    """Locate a bundled asset both in dev and inside a PyInstaller bundle."""
    if getattr(sys, "_MEIPASS", None):
        return os.path.join(sys._MEIPASS, "assets", name)
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "assets", name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Advanced MTR — network "
                                     "diagnostics for Windows")
    parser.add_argument("target", nargs="?", default="",
                        help="destination IP or hostname to trace on startup")
    args = parser.parse_args()

    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from . import APP_NAME
    from .gui import theme
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("AdvancedMTR")
    theme.apply_dark_theme(app)

    icon = QIcon()
    for candidate in ("icon.ico", "icon.png"):
        path = _asset_path(candidate)
        if os.path.exists(path):
            icon = QIcon(path)
            break
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow(icon=icon if not icon.isNull() else None,
                        initial_target=args.target)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
