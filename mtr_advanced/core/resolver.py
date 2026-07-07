"""Background reverse-DNS resolution with caching."""

from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional


class ReverseDnsResolver:
    """Resolves IPs to hostnames off the GUI thread, de-duplicated."""

    def __init__(self, on_resolved: Callable[[str, str], None]):
        self._on_resolved = on_resolved
        self._cache: Dict[str, Optional[str]] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=8,
                                        thread_name_prefix="rdns")

    def lookup(self, ip: str) -> Optional[str]:
        """Return the cached hostname, scheduling a lookup if unknown."""
        with self._lock:
            if ip in self._cache:
                return self._cache[ip]
            if ip in self._pending:
                return None
            self._pending.add(ip)
        self._pool.submit(self._resolve, ip)
        return None

    def _resolve(self, ip: str) -> None:
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except OSError:
            hostname = None
        with self._lock:
            self._cache[ip] = hostname
            self._pending.discard(ip)
        if hostname:
            self._on_resolved(ip, hostname)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
