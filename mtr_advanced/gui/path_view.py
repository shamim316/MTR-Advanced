"""Graphical network-path diagram.

Draws the traced route as a chain of nodes (source → hop 1 → … →
destination) laid out serpentine so long paths wrap. Node colour encodes
packet loss; labels show the IP plus hostname/location when known.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen)
from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView

from . import theme

NODE_R = 26          # node circle radius
CELL_W = 170         # horizontal spacing between nodes
CELL_H = 120         # vertical spacing between rows
LABEL_W = 150


class PathView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMinimumHeight(180)
        self._hops: List[dict] = []
        self._hostnames: Dict[str, str] = {}
        self._geo: Dict[str, dict] = {}
        self._target: str = ""
        self._dest_reached = False

    def update_path(self, snapshot: dict,
                    hostnames: Dict[str, str],
                    geo: Dict[str, dict]) -> None:
        self._hops = snapshot["hops"]
        self._hostnames = hostnames
        self._geo = geo
        self._target = snapshot["target"]
        self._dest_reached = snapshot["dest_reached"]
        self._rebuild()

    def clear_path(self) -> None:
        self._hops = []
        self._scene.clear()

    # -- drawing -------------------------------------------------------------

    def _rebuild(self) -> None:
        self._scene.clear()
        if not self._hops:
            return

        # Node list: local source + every hop.
        nodes = [{"kind": "source"}]
        for hop in self._hops:
            nodes.append({"kind": "hop", "hop": hop})

        cols = max(2, int(max(self.viewport().width() - 40, 400) // CELL_W))
        positions: List[QPointF] = []
        for i in range(len(nodes)):
            row, col = divmod(i, cols)
            if row % 2 == 1:            # serpentine: odd rows run right→left
                col = cols - 1 - col
            positions.append(QPointF(40 + col * CELL_W + CELL_W / 2,
                                     40 + row * CELL_H + CELL_H / 2))

        # Edges first, under the nodes.
        for i in range(len(nodes) - 1):
            self._draw_edge(positions[i], positions[i + 1],
                            nodes[i + 1])
        for i, node in enumerate(nodes):
            self._draw_node(positions[i], node,
                            is_last=(i == len(nodes) - 1))

        margin = 30
        self._scene.setSceneRect(
            self._scene.itemsBoundingRect().adjusted(-margin, -margin,
                                                     margin, margin))

    def _draw_edge(self, a: QPointF, b: QPointF, to_node: dict) -> None:
        hop = to_node.get("hop")
        silent = hop is not None and hop["address"] is None
        loss = hop["loss_pct"] if hop else 0.0
        color = theme.loss_node_color(loss, silent)
        pen = QPen(color, 2.5)
        if silent:
            pen.setStyle(Qt.PenStyle.DashLine)
        path = QPainterPath(a)
        mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
        # Slight curve so wrapped rows don't overlap their own labels.
        ctrl = QPointF(mid.x(), mid.y() - 12 if a.y() == b.y() else mid.y())
        path.quadTo(ctrl, b)
        self._scene.addPath(path, pen)

    def _draw_node(self, pos: QPointF, node: dict, is_last: bool) -> None:
        if node["kind"] == "source":
            fill = theme.COLOR_ACCENT
            title = "YOU"
            sub1, sub2 = "This PC", ""
            loss_txt = ""
            tooltip = "Trace source (this computer)"
        else:
            hop = node["hop"]
            ip = hop["address"]
            silent = ip is None
            fill = theme.loss_node_color(hop["loss_pct"], silent)
            title = str(hop["ttl"])
            sub1 = ip or "no response"
            host = self._hostnames.get(ip, "") if ip else ""
            g = self._geo.get(ip) if ip else None
            place = ""
            if g:
                place = g.get("country_code") or g.get("country") or ""
            sub2 = host[:24] if host else place
            if host and place:
                sub2 = f"{host[:18]} · {place}"
            loss_txt = (f"{hop['loss_pct']:.0f}% loss"
                        if not silent and hop["loss_pct"] > 0 else "")
            tooltip_lines = [f"Hop {hop['ttl']} — {ip or 'no response'}"]
            if host:
                tooltip_lines.append(host)
            if g and (g.get("country") or g.get("asn")):
                tooltip_lines.append(" · ".join(
                    p for p in (g.get("country"), g.get("asn")) if p))
            if not silent:
                tooltip_lines.append(
                    f"Loss {hop['loss_pct']:.1f}%  "
                    f"Avg {hop['avg']:.1f} ms" if hop["avg"] is not None
                    else f"Loss {hop['loss_pct']:.1f}%")
            tooltip = "\n".join(tooltip_lines)

        rect = QRectF(pos.x() - NODE_R, pos.y() - NODE_R,
                      NODE_R * 2, NODE_R * 2)
        ring = QPen(QColor(255, 255, 255, 70), 2)
        if is_last and self._dest_reached:
            ring = QPen(theme.COLOR_ACCENT, 3)   # destination gets a halo
        ellipse = self._scene.addEllipse(rect, ring, QBrush(fill))
        ellipse.setToolTip(tooltip)

        font = QFont()
        font.setBold(True)
        label = self._scene.addText(title, font)
        label.setDefaultTextColor(QColor("white"))
        lb = label.boundingRect()
        label.setPos(pos.x() - lb.width() / 2, pos.y() - lb.height() / 2)
        label.setToolTip(tooltip)

        small = QFont()
        small.setPointSizeF(max(small.pointSizeF() - 1.5, 7.0))
        y = pos.y() + NODE_R + 2
        for i, text in enumerate(t for t in (sub1, sub2, loss_txt) if t):
            item = self._scene.addText(text, small)
            color = (theme.COLOR_LOSS if text == loss_txt
                     else self.palette().text().color())
            item.setDefaultTextColor(color)
            b = item.boundingRect()
            item.setPos(pos.x() - b.width() / 2, y)
            item.setToolTip(tooltip)
            y += b.height() - 6

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._hops:
            self._rebuild()

    def export_image(self, path: str) -> bool:
        """Render the current diagram to a PNG file."""
        from PyQt6.QtGui import QImage

        rect = self._scene.sceneRect()
        if rect.isEmpty():
            return False
        image = QImage(int(rect.width()) * 2, int(rect.height()) * 2,
                       QImage.Format.Format_ARGB32)
        image.fill(self.palette().base().color())
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._scene.render(painter)
        painter.end()
        return image.save(path)
