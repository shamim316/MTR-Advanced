"""Main application window for Advanced MTR."""

from __future__ import annotations

import os
import time
from typing import Dict, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSpinBox, QSplitter, QStatusBar, QSystemTrayIcon, QTableView,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core.geoip import GeoInfo, GeoIpResolver
from ..core.resolver import ReverseDnsResolver
from ..core.tracer import MtrEngine, TraceConfig
from ..export import exporters
from . import theme
from .hop_table import COL_SPARK, HopTableModel, SparklineDelegate
from .path_view import PathView

ALERT_COOLDOWN_S = 60  # per-hop minimum gap between tray notifications


class EngineBridge(QObject):
    """Marshals engine/resolver callbacks onto the GUI thread."""
    snapshot_ready = pyqtSignal(dict)
    engine_error = pyqtSignal(str)
    hostname_ready = pyqtSignal(str, str)
    geo_ready = pyqtSignal(str, dict)


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        form = QFormLayout(self)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 60.0)
        self.interval.setSingleStep(0.1)
        self.interval.setSuffix(" s")
        self.interval.setValue(settings["interval_s"])
        form.addRow("Probe interval:", self.interval)

        self.timeout = QSpinBox()
        self.timeout.setRange(100, 10000)
        self.timeout.setSuffix(" ms")
        self.timeout.setValue(settings["timeout_ms"])
        form.addRow("Probe timeout:", self.timeout)

        self.packet_size = QSpinBox()
        self.packet_size.setRange(8, 1400)
        self.packet_size.setSuffix(" bytes")
        self.packet_size.setValue(settings["packet_size"])
        form.addRow("Packet size:", self.packet_size)

        self.max_hops = QSpinBox()
        self.max_hops.setRange(4, 64)
        self.max_hops.setValue(settings["max_hops"])
        form.addRow("Max hops:", self.max_hops)

        self.resolve_dns = QCheckBox("Resolve hostnames (reverse DNS)")
        self.resolve_dns.setChecked(settings["resolve_dns"])
        form.addRow(self.resolve_dns)

        self.geoip = QCheckBox("Look up location / AS (uses ip-api.com)")
        self.geoip.setChecked(settings["geoip"])
        form.addRow(self.geoip)

        self.alerts = QCheckBox("Notify on packet loss (system tray)")
        self.alerts.setChecked(settings["alerts"])
        form.addRow(self.alerts)

        self.alert_threshold = QDoubleSpinBox()
        self.alert_threshold.setRange(0.5, 100.0)
        self.alert_threshold.setSuffix(" %")
        self.alert_threshold.setValue(settings["alert_threshold"])
        form.addRow("Alert when loss exceeds:", self.alert_threshold)

        self.dark_theme = QCheckBox("Dark theme")
        self.dark_theme.setChecked(settings["dark_theme"])
        form.addRow(self.dark_theme)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "interval_s": self.interval.value(),
            "timeout_ms": self.timeout.value(),
            "packet_size": self.packet_size.value(),
            "max_hops": self.max_hops.value(),
            "resolve_dns": self.resolve_dns.isChecked(),
            "geoip": self.geoip.isChecked(),
            "alerts": self.alerts.isChecked(),
            "alert_threshold": self.alert_threshold.value(),
            "dark_theme": self.dark_theme.isChecked(),
        }


class MainWindow(QMainWindow):
    def __init__(self, icon: Optional[QIcon] = None,
                 initial_target: str = ""):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1240, 800)
        if icon:
            self.setWindowIcon(icon)

        self.settings = {
            "interval_s": 1.0,
            "timeout_ms": 1000,
            "packet_size": 56,
            "max_hops": 30,
            "resolve_dns": True,
            "geoip": True,
            "alerts": True,
            "alert_threshold": 5.0,
            "dark_theme": True,
        }

        self.engine: Optional[MtrEngine] = None
        self.bridge = EngineBridge()
        self.bridge.snapshot_ready.connect(self._on_snapshot)
        self.bridge.engine_error.connect(self._on_engine_error)
        self.bridge.hostname_ready.connect(self._on_hostname)
        self.bridge.geo_ready.connect(self._on_geo)

        self.rdns = ReverseDnsResolver(
            lambda ip, host: self.bridge.hostname_ready.emit(ip, host))
        self.geoip = GeoIpResolver(
            lambda ip, info: self.bridge.geo_ready.emit(ip, vars(info)))

        self._hostnames: Dict[str, str] = {}
        self._geo: Dict[str, dict] = {}
        self._last_snapshot: Optional[dict] = None
        self._alert_last_sent: Dict[int, float] = {}

        self._build_ui(initial_target)
        self._build_tray(icon)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_status)

    # -- UI construction -----------------------------------------------------

    def _build_ui(self, initial_target: str) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 6)

        # Input row
        row = QHBoxLayout()
        row.addWidget(QLabel("Target:"))
        self.target_edit = QLineEdit(initial_target)
        self.target_edit.setPlaceholderText(
            "Enter destination IP address or hostname — e.g. 8.8.8.8 or example.com")
        self.target_edit.returnPressed.connect(self._on_start_stop)
        row.addWidget(self.target_edit, stretch=1)

        self.family_combo = QComboBox()
        self.family_combo.addItems(["Auto", "IPv4", "IPv6"])
        self.family_combo.setToolTip("Address family used to resolve the target")
        row.addWidget(self.family_combo)

        self.start_btn = QPushButton("▶  Start")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._on_start_stop)
        row.addWidget(self.start_btn)

        self.pause_btn = QPushButton("⏸  Pause")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_resume)
        row.addWidget(self.pause_btn)

        self.reset_btn = QPushButton("↺  Reset")
        self.reset_btn.setEnabled(False)
        self.reset_btn.setToolTip("Clear accumulated statistics")
        self.reset_btn.clicked.connect(self._on_reset)
        row.addWidget(self.reset_btn)

        self.export_btn = QToolButton()
        self.export_btn.setText("⬇  Export")
        self.export_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self)
        for label, fmt in (("CSV (.csv)", "csv"),
                           ("Plain text (.txt)", "txt"),
                           ("Markdown (.md)", "md")):
            action = QAction(label, self)
            action.triggered.connect(
                lambda _=False, f=fmt: self._export(f))
            export_menu.addAction(action)
        export_menu.addSeparator()
        diagram_action = QAction("Path diagram (.png)", self)
        diagram_action.triggered.connect(self._export_diagram)
        export_menu.addAction(diagram_action)
        self.export_btn.setMenu(export_menu)
        self.export_btn.setEnabled(False)
        row.addWidget(self.export_btn)

        settings_btn = QPushButton("⚙  Settings")
        settings_btn.clicked.connect(self._open_settings)
        row.addWidget(settings_btn)
        layout.addLayout(row)

        # Table + path view
        self.model = HopTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_SPARK,
                                            SparklineDelegate(self.table))
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SPARK, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)

        self.path_view = PathView()

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.path_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_label = QLabel("Enter a target and press Start.")
        self.status.addWidget(self.status_label)

        quit_shortcut = QAction(self)
        quit_shortcut.setShortcut(QKeySequence("Ctrl+Q"))
        quit_shortcut.triggered.connect(self.close)
        self.addAction(quit_shortcut)

    def _build_tray(self, icon: Optional[QIcon]) -> None:
        self.tray: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icon or self.windowIcon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = QAction("Show window", menu)
        show_action.triggered.connect(self._restore_from_tray)
        menu.addAction(show_action)
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore_from_tray()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -- engine control --------------------------------------------------------

    def _on_start_stop(self) -> None:
        if self.engine and self.engine.running:
            self._stop_trace()
            return
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, APP_NAME,
                                "Please enter a destination IP address "
                                "or hostname.")
            return
        family = self.family_combo.currentText().lower()
        config = TraceConfig(
            target=target,
            family=family,
            max_hops=self.settings["max_hops"],
            interval_s=self.settings["interval_s"],
            timeout_ms=self.settings["timeout_ms"],
            packet_size=self.settings["packet_size"],
        )
        self.model.set_snapshot([])
        self.model.clear_enrichment()
        self._hostnames.clear()
        self._geo.clear()
        self._alert_last_sent.clear()
        self.path_view.clear_path()
        self.geoip.enabled = self.settings["geoip"]

        self.engine = MtrEngine(
            config,
            on_snapshot=self.bridge.snapshot_ready.emit,
            on_error=self.bridge.engine_error.emit,
        )
        self.engine.start()
        if not self.engine.running:
            return  # resolution failed; error dialog already queued
        self.start_btn.setText("⏹  Stop")
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("⏸  Pause")
        self.reset_btn.setEnabled(True)
        self.target_edit.setEnabled(False)
        self.family_combo.setEnabled(False)
        self.status_label.setText(f"Tracing {target} …")
        self._elapsed_timer.start()

    def _stop_trace(self) -> None:
        if self.engine:
            self.engine.stop()
        self._elapsed_timer.stop()
        self.start_btn.setText("▶  Start")
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("⏸  Pause")
        self.target_edit.setEnabled(True)
        self.family_combo.setEnabled(True)
        self._update_status(stopped=True)

    def _on_pause_resume(self) -> None:
        if not self.engine:
            return
        if self.engine.paused:
            self.engine.resume()
            self.pause_btn.setText("⏸  Pause")
        else:
            self.engine.pause()
            self.pause_btn.setText("▶  Resume")
        self._update_status()

    def _on_reset(self) -> None:
        if self.engine:
            self.engine.reset_stats()
        self._alert_last_sent.clear()

    def _on_engine_error(self, message: str) -> None:
        self._stop_trace()
        QMessageBox.critical(self, APP_NAME, message)

    # -- data flow ---------------------------------------------------------------

    def _on_snapshot(self, snapshot: dict) -> None:
        self._last_snapshot = snapshot
        self.model.set_snapshot(snapshot["hops"])
        self.export_btn.setEnabled(bool(snapshot["hops"]))

        for hop in snapshot["hops"]:
            ip = hop["address"]
            if not ip:
                continue
            if self.settings["resolve_dns"] and ip not in self._hostnames:
                host = self.rdns.lookup(ip)
                if host:
                    self._on_hostname(ip, host)
            if ip not in self._geo:
                info = self.geoip.lookup(ip)
                if info:
                    self._on_geo(ip, vars(info))

        self.path_view.update_path(snapshot, self._hostnames, self._geo)
        self._check_alerts(snapshot)
        self._update_status()

    def _on_hostname(self, ip: str, hostname: str) -> None:
        self._hostnames[ip] = hostname
        self.model.set_hostname(ip, hostname)
        if self._last_snapshot:
            self.path_view.update_path(self._last_snapshot,
                                       self._hostnames, self._geo)

    def _on_geo(self, ip: str, info: dict) -> None:
        self._geo[ip] = info
        self.model.set_geo(ip, info)
        if self._last_snapshot:
            self.path_view.update_path(self._last_snapshot,
                                       self._hostnames, self._geo)

    def _check_alerts(self, snapshot: dict) -> None:
        if not (self.settings["alerts"] and self.tray):
            return
        threshold = self.settings["alert_threshold"]
        now = time.time()
        for hop in snapshot["hops"]:
            # Require a responding hop and a meaningful sample size.
            if hop["address"] is None or hop["sent"] < 10:
                continue
            if hop["loss_pct"] < threshold:
                continue
            last = self._alert_last_sent.get(hop["ttl"], 0.0)
            if now - last < ALERT_COOLDOWN_S:
                continue
            self._alert_last_sent[hop["ttl"]] = now
            self.tray.showMessage(
                f"{APP_NAME}: packet loss detected",
                f"Hop {hop['ttl']} ({hop['address']}) is losing "
                f"{hop['loss_pct']:.1f}% of probes on the path to "
                f"{snapshot['target']}.",
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )

    def _update_status(self, stopped: bool = False) -> None:
        snap = self._last_snapshot
        if not snap:
            return
        elapsed = int(time.time() - snap["started_at"])
        state = ("stopped" if stopped
                 else "paused" if (self.engine and self.engine.paused)
                 else "running")
        dest = "reached" if snap["dest_reached"] else "not reached yet"
        lossy = sum(1 for h in snap["hops"]
                    if h["address"] and h["loss_pct"] > 0)
        self.status_label.setText(
            f"{snap['target']} ({snap['resolved_ip']}) — {state} · "
            f"round {snap['rounds']} · {len(snap['hops'])} hops "
            f"(destination {dest}) · {lossy} hop(s) with loss · "
            f"elapsed {elapsed // 60:02d}:{elapsed % 60:02d}"
        )

    # -- export ---------------------------------------------------------------

    def _export(self, fmt: str) -> None:
        if not self._last_snapshot:
            return
        filters = {"csv": "CSV files (*.csv)",
                   "txt": "Text files (*.txt)",
                   "md": "Markdown files (*.md)"}
        target = self._last_snapshot["target"].replace(":", "_")
        default = os.path.join(
            os.path.expanduser("~"),
            f"mtr_{target}_{time.strftime('%Y%m%d_%H%M%S')}.{fmt}")
        path, _ = QFileDialog.getSaveFileName(self, "Export results",
                                              default, filters[fmt])
        if not path:
            return
        content = exporters.EXPORTERS[fmt](self._last_snapshot,
                                           self._hostnames, self._geo)
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME,
                                 f"Could not write file:\n{exc}")
            return
        self.status.showMessage(f"Exported to {path}", 5000)

    def _export_diagram(self) -> None:
        if not self._last_snapshot:
            return
        target = self._last_snapshot["target"].replace(":", "_")
        default = os.path.join(
            os.path.expanduser("~"),
            f"mtr_path_{target}_{time.strftime('%Y%m%d_%H%M%S')}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Export path diagram",
                                              default, "PNG images (*.png)")
        if not path:
            return
        if self.path_view.export_image(path):
            self.status.showMessage(f"Diagram exported to {path}", 5000)
        else:
            QMessageBox.critical(self, APP_NAME, "Could not render diagram.")

    # -- settings / shutdown -------------------------------------------------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = dialog.values()
        theme_changed = new["dark_theme"] != self.settings["dark_theme"]
        self.settings.update(new)
        self.geoip.enabled = new["geoip"]
        if self.engine:
            # Interval/timeout tweaks apply live; the rest on next start.
            self.engine.config.interval_s = new["interval_s"]
            self.engine.config.timeout_ms = new["timeout_ms"]
        if theme_changed:
            app = QApplication.instance()
            if new["dark_theme"]:
                theme.apply_dark_theme(app)
            else:
                theme.apply_light_theme(app)

    def closeEvent(self, event) -> None:
        if self.engine:
            self.engine.stop()
        self.rdns.shutdown()
        self.geoip.shutdown()
        if self.tray:
            self.tray.hide()
        super().closeEvent(event)
