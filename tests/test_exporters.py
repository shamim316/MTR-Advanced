import csv
import io
import time

from mtr_advanced.export.exporters import to_csv, to_markdown, to_txt


def make_snapshot():
    return {
        "target": "example.com",
        "resolved_ip": "93.184.216.34",
        "rounds": 25,
        "dest_reached": True,
        "started_at": time.mktime((2026, 7, 7, 12, 0, 0, 0, 0, -1)),
        "interval_s": 1.0,
        "packet_size": 56,
        "hops": [
            {
                "ttl": 1, "address": "192.168.1.1",
                "all_addresses": ["192.168.1.1"],
                "sent": 25, "received": 25, "loss_pct": 0.0,
                "last": 1.2, "avg": 1.5, "best": 0.9, "worst": 3.1,
                "stdev": 0.4, "jitter": 0.3, "history": [1.2],
            },
            {
                "ttl": 2, "address": None, "all_addresses": [],
                "sent": 25, "received": 0, "loss_pct": 100.0,
                "last": None, "avg": None, "best": None, "worst": None,
                "stdev": None, "jitter": None, "history": [None],
            },
            {
                "ttl": 3, "address": "93.184.216.34",
                "all_addresses": ["93.184.216.34"],
                "sent": 25, "received": 22, "loss_pct": 12.0,
                "last": 20.4, "avg": 21.0, "best": 18.2, "worst": 40.0,
                "stdev": 4.2, "jitter": 2.1, "history": [20.4],
            },
        ],
    }


HOSTNAMES = {"192.168.1.1": "router.lan",
             "93.184.216.34": "example.com"}
GEO = {"93.184.216.34": {"country": "United States",
                         "asn": "AS15133 Edgecast Inc."}}


def test_csv_round_trip():
    out = to_csv(make_snapshot(), HOSTNAMES, GEO)
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0][0] == "Hop"
    assert len(rows) == 4  # header + 3 hops
    hop1 = rows[1]
    assert hop1[0] == "1"
    assert hop1[1] == "192.168.1.1"
    assert hop1[2] == "router.lan"
    assert hop1[5] == "0.0"
    silent = rows[2]
    assert silent[1] == "???"
    assert silent[5] == "100.0"
    lossy = rows[3]
    assert lossy[3] == "United States"
    assert lossy[4] == "AS15133 Edgecast Inc."
    assert lossy[5] == "12.0"


def test_txt_report_layout():
    out = to_txt(make_snapshot(), HOSTNAMES, GEO)
    lines = out.splitlines()
    assert lines[0] == "Advanced MTR report"
    assert "example.com (93.184.216.34)" in lines[1]
    assert any(line.startswith("Hop") for line in lines)
    assert any("93.184.216.34" in line and "12.0" in line for line in lines)


def test_markdown_flags_lossy_hops():
    out = to_markdown(make_snapshot(), HOSTNAMES, GEO)
    assert "# Advanced MTR report" in out
    assert "| Hop |" in out
    # Lossy hop is bolded and listed in the summary section
    assert "**12.0** ⚠️" in out
    assert "## Hops with packet loss" in out
    assert "Hop 3" in out
    # Zero-loss hop is not flagged
    assert "**0.0**" not in out


def test_exports_without_enrichment():
    snap = make_snapshot()
    for fn in (to_csv, to_txt, to_markdown):
        out = fn(snap)
        assert "192.168.1.1" in out
