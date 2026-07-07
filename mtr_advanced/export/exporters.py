"""Export trace results to CSV, plain-text (classic mtr report) or Markdown.

Each function takes an engine snapshot (see ``MtrEngine._snapshot_locked``)
plus optional enrichment maps {ip: hostname} and {ip: GeoInfo-like label},
and returns the file content as a string.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Dict, Optional

_COLUMNS = [
    ("Hop", "ttl"),
    ("Address", "address"),
    ("Hostname", "hostname"),
    ("Location", "location"),
    ("AS/Org", "asn"),
    ("Loss%", "loss_pct"),
    ("Sent", "sent"),
    ("Recv", "received"),
    ("Last(ms)", "last"),
    ("Avg(ms)", "avg"),
    ("Best(ms)", "best"),
    ("Worst(ms)", "worst"),
    ("StDev(ms)", "stdev"),
    ("Jitter(ms)", "jitter"),
]


def _fmt(value, key: str) -> str:
    if value is None:
        return "-"
    if key == "loss_pct":
        return f"{value:.1f}"
    if key in ("last", "avg", "best", "worst", "stdev", "jitter"):
        return f"{value:.1f}"
    return str(value)


def _rows(snapshot: dict,
          hostnames: Optional[Dict[str, str]] = None,
          geo: Optional[Dict[str, dict]] = None) -> list[dict]:
    hostnames = hostnames or {}
    geo = geo or {}
    rows = []
    for hop in snapshot["hops"]:
        ip = hop["address"]
        g = geo.get(ip, {}) if ip else {}
        rows.append({
            "ttl": hop["ttl"],
            "address": ip or "???",
            "hostname": (hostnames.get(ip) or "-") if ip else "-",
            "location": g.get("country") or "-",
            "asn": g.get("asn") or g.get("org") or "-",
            "loss_pct": hop["loss_pct"],
            "sent": hop["sent"],
            "received": hop["received"],
            "last": hop["last"],
            "avg": hop["avg"],
            "best": hop["best"],
            "worst": hop["worst"],
            "stdev": hop["stdev"],
            "jitter": hop["jitter"],
        })
    return rows


def _header_lines(snapshot: dict) -> list[str]:
    started = datetime.fromtimestamp(snapshot["started_at"])
    return [
        "Advanced MTR report",
        f"Target:   {snapshot['target']} ({snapshot['resolved_ip']})",
        f"Started:  {started:%Y-%m-%d %H:%M:%S}",
        f"Rounds:   {snapshot['rounds']}   "
        f"Interval: {snapshot['interval_s']:g}s   "
        f"Packet size: {snapshot['packet_size']} bytes",
    ]


def to_csv(snapshot: dict, hostnames=None, geo=None) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([label for label, _ in _COLUMNS])
    for row in _rows(snapshot, hostnames, geo):
        writer.writerow([_fmt(row[key], key) for _, key in _COLUMNS])
    return buf.getvalue()


def to_txt(snapshot: dict, hostnames=None, geo=None) -> str:
    rows = _rows(snapshot, hostnames, geo)
    table = [[label for label, _ in _COLUMNS]]
    table += [[_fmt(row[key], key) for _, key in _COLUMNS] for row in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(_COLUMNS))]
    lines = _header_lines(snapshot)
    lines.append("")
    for r_idx, row in enumerate(table):
        lines.append("  ".join(cell.ljust(widths[i])
                               for i, cell in enumerate(row)).rstrip())
        if r_idx == 0:
            lines.append("  ".join("-" * widths[i]
                                   for i in range(len(_COLUMNS))))
    return "\n".join(lines) + "\n"


def to_markdown(snapshot: dict, hostnames=None, geo=None) -> str:
    rows = _rows(snapshot, hostnames, geo)
    started = datetime.fromtimestamp(snapshot["started_at"])
    lines = [
        "# Advanced MTR report",
        "",
        f"- **Target:** `{snapshot['target']}` ({snapshot['resolved_ip']})",
        f"- **Started:** {started:%Y-%m-%d %H:%M:%S}",
        f"- **Rounds:** {snapshot['rounds']}",
        f"- **Interval:** {snapshot['interval_s']:g}s — "
        f"**Packet size:** {snapshot['packet_size']} bytes",
        "",
        "| " + " | ".join(label for label, _ in _COLUMNS) + " |",
        "|" + "|".join("---" for _ in _COLUMNS) + "|",
    ]
    for row in rows:
        cells = []
        for _, key in _COLUMNS:
            cell = _fmt(row[key], key)
            # Flag lossy hops so they stand out in rendered Markdown.
            if key == "loss_pct" and row["loss_pct"] > 0:
                cell = f"**{cell}** ⚠️"
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lossy = [r for r in rows if r["loss_pct"] > 0 and r["address"] != "???"]
    if lossy:
        lines.append("## Hops with packet loss")
        lines.append("")
        for r in lossy:
            lines.append(f"- Hop {r['ttl']} — `{r['address']}` "
                         f"({r['hostname']}): **{r['loss_pct']:.1f}%** loss")
        lines.append("")
    return "\n".join(lines)


EXPORTERS = {
    "csv": to_csv,
    "txt": to_txt,
    "md": to_markdown,
}
