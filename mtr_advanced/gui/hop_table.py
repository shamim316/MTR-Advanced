"""Hop table: Qt model over engine snapshots, loss auto-highlighting and a
sparkline delegate showing recent RTT history per hop."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from . import theme

COLUMNS = [
    "Hop", "Address", "Hostname", "Location", "AS / Org",
    "Loss %", "Snt", "Recv", "Last", "Avg", "Best", "Wrst",
    "StDev", "Jitter", "Latency history",
]
COL_HOP, COL_ADDR, COL_HOST, COL_LOC, COL_ASN, COL_LOSS, COL_SENT, \
    COL_RECV, COL_LAST, COL_AVG, COL_BEST, COL_WORST, COL_STDEV, \
    COL_JITTER, COL_SPARK = range(len(COLUMNS))

_MS_COLS = {COL_LAST, COL_AVG, COL_BEST, COL_WORST, COL_STDEV, COL_JITTER}


class HopTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hops: List[dict] = []
        self._hostnames: Dict[str, str] = {}
        self._geo: Dict[str, dict] = {}

    # -- update entry points -------------------------------------------------

    def set_snapshot(self, hops: List[dict]) -> None:
        old_len = len(self._hops)
        self._hops = hops
        if len(hops) != old_len:
            self.beginResetModel()
            self.endResetModel()
        elif hops:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(hops) - 1, len(COLUMNS) - 1),
            )

    def set_hostname(self, ip: str, hostname: str) -> None:
        self._hostnames[ip] = hostname
        self._refresh_column(COL_HOST)

    def set_geo(self, ip: str, geo: dict) -> None:
        self._geo[ip] = geo
        self._refresh_column(COL_LOC)
        self._refresh_column(COL_ASN)

    def _refresh_column(self, col: int) -> None:
        if self._hops:
            self.dataChanged.emit(self.index(0, col),
                                  self.index(len(self._hops) - 1, col))

    def clear_enrichment(self) -> None:
        self._hostnames.clear()
        self._geo.clear()

    def hop_at(self, row: int) -> Optional[dict]:
        return self._hops[row] if 0 <= row < len(self._hops) else None

    def hostname_for(self, ip: Optional[str]) -> Optional[str]:
        return self._hostnames.get(ip) if ip else None

    def geo_for(self, ip: Optional[str]) -> Optional[dict]:
        return self._geo.get(ip) if ip else None

    # -- QAbstractTableModel ---------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._hops)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole \
                and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        hop = self._hops[index.row()]
        col = index.column()
        ip = hop["address"]
        silent = ip is None

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(hop, col, ip)
        if role == Qt.ItemDataRole.BackgroundRole:
            if not silent:
                return theme.loss_tint(hop["loss_pct"])
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            if silent:
                return theme.COLOR_SILENT
            if col == COL_LOSS and hop["loss_pct"] > 0:
                return theme.COLOR_LOSS
            return None
        if role == Qt.ItemDataRole.FontRole:
            if col == COL_LOSS and not silent and hop["loss_pct"] > 0:
                font = QFont()
                font.setBold(True)
                return font
            if silent:
                font = QFont()
                font.setItalic(True)
                return font
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in _MS_COLS or col in (COL_HOP, COL_LOSS, COL_SENT, COL_RECV):
                return int(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(hop, ip)
        if role == Qt.ItemDataRole.UserRole and col == COL_SPARK:
            return hop["history"]
        return None

    def _display(self, hop: dict, col: int, ip: Optional[str]) -> Any:
        def ms(v):
            return f"{v:.1f}" if v is not None else "—"

        if col == COL_HOP:
            return hop["ttl"]
        if col == COL_ADDR:
            return ip or "No response"
        if col == COL_HOST:
            return self._hostnames.get(ip, "") if ip else ""
        if col == COL_LOC:
            g = self._geo.get(ip) if ip else None
            return g.get("country", "") if g else ""
        if col == COL_ASN:
            g = self._geo.get(ip) if ip else None
            return (g.get("asn") or g.get("org", "")) if g else ""
        if col == COL_LOSS:
            return f"{hop['loss_pct']:.1f}%"
        if col == COL_SENT:
            return hop["sent"]
        if col == COL_RECV:
            return hop["received"]
        if col == COL_LAST:
            return ms(hop["last"])
        if col == COL_AVG:
            return ms(hop["avg"])
        if col == COL_BEST:
            return ms(hop["best"])
        if col == COL_WORST:
            return ms(hop["worst"])
        if col == COL_STDEV:
            return ms(hop["stdev"])
        if col == COL_JITTER:
            return ms(hop["jitter"])
        return None

    def _tooltip(self, hop: dict, ip: Optional[str]) -> str:
        if ip is None:
            return (f"Hop {hop['ttl']}: no responses received.\n"
                    "The router at this hop may drop or rate-limit "
                    "TTL-expired replies — this is common and only a "
                    "problem if later hops also show loss.")
        lines = [f"Hop {hop['ttl']} — {ip}"]
        host = self._hostnames.get(ip)
        if host:
            lines.append(host)
        g = self._geo.get(ip)
        if g:
            label = " · ".join(p for p in (g.get("country"),
                                           g.get("asn") or g.get("org")) if p)
            if label:
                lines.append(label)
        if len(hop["all_addresses"]) > 1:
            lines.append("Also seen: "
                         + ", ".join(a for a in hop["all_addresses"] if a != ip))
        lines.append(f"Loss {hop['loss_pct']:.1f}% "
                     f"({hop['received']}/{hop['sent']} answered)")
        return "\n".join(lines)


class SparklineDelegate(QStyledItemDelegate):
    """Paints per-hop RTT history: a line for RTTs, red ticks for losses."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        history = index.data(Qt.ItemDataRole.UserRole)
        if not history:
            super().paint(painter, option, index)
            return
        bg = index.data(Qt.ItemDataRole.BackgroundRole)
        painter.save()
        if bg is not None:
            painter.fillRect(option.rect, bg)
        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        values = [v for v in history if v is not None]
        vmax = max(values) if values else 1.0
        vmax = max(vmax, 1.0)
        n = len(history)
        step = rect.width() / max(n - 1, 1)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Lost probes: short red ticks along the baseline.
        loss_pen = QPen(theme.COLOR_LOSS, 1.5)
        painter.setPen(loss_pen)
        for i, v in enumerate(history):
            if v is None:
                x = rect.left() + i * step
                painter.drawLine(int(x), int(rect.bottom() - 3),
                                 int(x), int(rect.bottom()))

        # RTT line.
        painter.setPen(QPen(theme.COLOR_ACCENT, 1.2))
        prev = None
        for i, v in enumerate(history):
            if v is None:
                prev = None
                continue
            x = rect.left() + i * step
            y = rect.bottom() - (v / vmax) * rect.height()
            if prev is not None:
                painter.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
            prev = (x, y)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setWidth(max(size.width(), 160))
        return size
