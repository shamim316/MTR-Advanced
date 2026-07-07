"""The MTR engine: repeated TTL-stepped probing rounds with live stats.

Framework-agnostic — the GUI subscribes via a callback, and the engine
runs in its own thread. Each round probes every TTL from 1 up to the
discovered path length concurrently, updates :class:`HopStats`, then
publishes a plain-data snapshot.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .probe import ProbeResult, ProbeStatus, probe, resolve_target
from .stats import HopStats

SnapshotCallback = Callable[[dict], None]


@dataclass
class TraceConfig:
    target: str
    family: str = "auto"          # "auto" | "ipv4" | "ipv6"
    max_hops: int = 30
    interval_s: float = 1.0
    timeout_ms: int = 1000
    packet_size: int = 56
    # After the destination answers, only probe up to its TTL (+ a small
    # margin so path changes are still noticed).
    discovery_margin: int = 2


@dataclass
class TraceState:
    resolved_ip: str = ""
    dest_ttl: Optional[int] = None   # TTL at which the destination replied
    rounds: int = 0
    started_at: float = field(default_factory=time.time)


class MtrEngine:
    """Runs mtr-style probing rounds on a background thread."""

    def __init__(self, config: TraceConfig,
                 on_snapshot: SnapshotCallback,
                 on_error: Optional[Callable[[str], None]] = None):
        self.config = config
        self.on_snapshot = on_snapshot
        self.on_error = on_error or (lambda msg: None)
        self.state = TraceState()
        self._hops: Dict[int, HopStats] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        try:
            ip, _family = resolve_target(self.config.target, self.config.family)
        except OSError as exc:
            self.on_error(f"Cannot resolve '{self.config.target}': {exc}")
            return
        self.state = TraceState(resolved_ip=ip)
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="mtr-engine")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def reset_stats(self) -> None:
        with self._lock:
            self._hops.clear()
            self.state.rounds = 0
            self.state.dest_ttl = None
            self.state.started_at = time.time()

    # -- probing loop ------------------------------------------------------

    def _probe_range(self) -> int:
        if self.state.dest_ttl is not None:
            return min(self.state.dest_ttl + self.config.discovery_margin,
                       self.config.max_hops)
        return self.config.max_hops

    def _run(self) -> None:
        cfg = self.config
        while not self._stop.is_set():
            if self._pause.is_set():
                time.sleep(0.2)
                continue
            round_start = time.perf_counter()
            max_ttl = self._probe_range()
            ttls = list(range(1, max_ttl + 1))

            with self._lock:
                for ttl in ttls:
                    self._hops.setdefault(ttl, HopStats(ttl)).record_sent()

            with ThreadPoolExecutor(max_workers=min(len(ttls), 32),
                                    thread_name_prefix="probe") as pool:
                futures = {
                    ttl: pool.submit(probe, self.state.resolved_ip, ttl,
                                     cfg.timeout_ms, cfg.packet_size)
                    for ttl in ttls
                }
                results: Dict[int, ProbeResult] = {}
                for ttl, fut in futures.items():
                    try:
                        results[ttl] = fut.result()
                    except Exception as exc:  # never let one probe kill the loop
                        results[ttl] = ProbeResult(ProbeStatus.ERROR,
                                                   detail=str(exc))

            with self._lock:
                self._apply_results(results)
                self.state.rounds += 1
                snapshot = self._snapshot_locked()

            self.on_snapshot(snapshot)

            elapsed = time.perf_counter() - round_start
            remaining = cfg.interval_s - elapsed
            if remaining > 0:
                self._stop.wait(remaining)

    def _apply_results(self, results: Dict[int, ProbeResult]) -> None:
        dest_ttl: Optional[int] = None
        for ttl, res in sorted(results.items()):
            hop = self._hops[ttl]
            if res.answered and res.responder:
                hop.record_reply(res.responder, res.rtt_ms or 0.0)
                if res.status == ProbeStatus.REPLY and dest_ttl is None:
                    dest_ttl = ttl
            elif res.status == ProbeStatus.ERROR and res.detail:
                hop.record_timeout()
                self.on_error(res.detail)
                self._stop.set()
                return
            else:
                hop.record_timeout()
        if dest_ttl is not None:
            self.state.dest_ttl = dest_ttl
            # Drop stale hops beyond the destination.
            for ttl in [t for t in self._hops if t > dest_ttl]:
                del self._hops[ttl]

    def _snapshot_locked(self) -> dict:
        visible = self.state.dest_ttl or max(self._hops, default=0)
        hops: List[dict] = [
            self._hops[ttl].snapshot()
            for ttl in sorted(self._hops)
            if ttl <= visible
        ]
        # Trim trailing silent hops (never answered) when the destination
        # hasn't been reached, so the table doesn't fill with 30 dead rows —
        # keep one so the user sees probing is ongoing.
        if self.state.dest_ttl is None:
            while len(hops) > 1 and hops[-1]["address"] is None \
                    and hops[-2]["address"] is None:
                hops.pop()
        return {
            "target": self.config.target,
            "resolved_ip": self.state.resolved_ip,
            "rounds": self.state.rounds,
            "dest_reached": self.state.dest_ttl is not None,
            "started_at": self.state.started_at,
            "interval_s": self.config.interval_s,
            "packet_size": self.config.packet_size,
            "hops": hops,
        }
