"""GeoIP / ASN enrichment using the free ip-api.com batch endpoint.

Lookups run on a background thread, are cached for the process lifetime,
and degrade gracefully when offline. Private/reserved addresses are
labelled locally without any network call.

Note: the free tier of ip-api.com is HTTP-only and rate-limited
(~15 batch requests/minute); we batch aggressively and cache, which keeps
a typical trace to one or two requests total.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

_BATCH_URL = "http://ip-api.com/batch?fields=status,country,countryCode,as,org,query"
_MIN_REQUEST_GAP_S = 4.5  # stay safely under the free-tier rate limit


@dataclass
class GeoInfo:
    country: str = ""
    country_code: str = ""
    asn: str = ""       # e.g. "AS15169 Google LLC"
    org: str = ""

    @property
    def label(self) -> str:
        parts = [p for p in (self.country, self.asn or self.org) if p]
        return " · ".join(parts)


class GeoIpResolver:
    """Batched, cached GeoIP/ASN lookups off the GUI thread."""

    def __init__(self, on_resolved: Callable[[str, GeoInfo], None],
                 enabled: bool = True):
        self.enabled = enabled
        self._on_resolved = on_resolved
        self._cache: Dict[str, GeoInfo] = {}
        self._queue: List[str] = []
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_request = 0.0
        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="geoip")
        self._thread.start()

    def lookup(self, ip: str) -> Optional[GeoInfo]:
        """Return cached info, scheduling a batch lookup if unknown."""
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
            info = GeoInfo(country="Private network")
            self._cache[ip] = info
            return info
        with self._lock:
            if ip in self._cache:
                return self._cache[ip]
            if self.enabled and ip not in self._queued:
                self._queued.add(ip)
                self._queue.append(ip)
                self._wake.set()
        return None

    def _worker(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            with self._lock:
                if not self._queue:
                    self._wake.clear()
                    continue
                batch = self._queue[:100]
                del self._queue[:100]
            gap = _MIN_REQUEST_GAP_S - (time.time() - self._last_request)
            if gap > 0:
                if self._stop.wait(gap):
                    return
            self._request_batch(batch)

    def _request_batch(self, ips: List[str]) -> None:
        body = json.dumps(ips).encode()
        req = urllib.request.Request(
            _BATCH_URL, data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "AdvancedMTR/1.0"},
        )
        self._last_request = time.time()
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                results = json.loads(resp.read().decode())
        except (OSError, ValueError):
            # Offline or rate-limited: forget these IPs so a later round
            # can retry them.
            with self._lock:
                for ip in ips:
                    self._queued.discard(ip)
            return
        with self._lock:
            for entry in results:
                ip = entry.get("query", "")
                if entry.get("status") != "success":
                    self._queued.discard(ip)
                    continue
                info = GeoInfo(
                    country=entry.get("country", ""),
                    country_code=entry.get("countryCode", ""),
                    asn=entry.get("as", ""),
                    org=entry.get("org", ""),
                )
                self._cache[ip] = info
                self._queued.discard(ip)
        for ip in ips:
            info = self._cache.get(ip)
            if info:
                self._on_resolved(ip, info)

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
