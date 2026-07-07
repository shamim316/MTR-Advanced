"""Per-hop statistics accumulation, modelled on classic mtr columns."""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, Optional


HISTORY_LEN = 120  # RTT samples kept per hop for sparklines


@dataclass
class HopStats:
    """Accumulated probe statistics for one TTL step of the path."""

    ttl: int
    sent: int = 0
    received: int = 0
    last: Optional[float] = None
    best: Optional[float] = None
    worst: Optional[float] = None
    _sum: float = 0.0
    _sum_sq: float = 0.0
    _jitter_sum: float = 0.0
    _jitter_pairs: int = 0
    _prev_rtt: Optional[float] = None
    # A hop can answer from several addresses (per-packet load balancing);
    # track them all and display the most frequent one.
    addresses: Counter = field(default_factory=Counter)
    # None entry means the probe for that round was lost.
    history: Deque[Optional[float]] = field(
        default_factory=lambda: deque(maxlen=HISTORY_LEN)
    )

    @property
    def address(self) -> Optional[str]:
        """Most frequently seen responder address, or None if silent."""
        if not self.addresses:
            return None
        return self.addresses.most_common(1)[0][0]

    @property
    def loss_pct(self) -> float:
        if self.sent == 0:
            return 0.0
        return 100.0 * (self.sent - self.received) / self.sent

    @property
    def avg(self) -> Optional[float]:
        if self.received == 0:
            return None
        return self._sum / self.received

    @property
    def stdev(self) -> Optional[float]:
        if self.received < 2:
            return None
        n = self.received
        var = (self._sum_sq - (self._sum * self._sum) / n) / (n - 1)
        return math.sqrt(max(var, 0.0))

    @property
    def jitter(self) -> Optional[float]:
        """Mean absolute difference between consecutive RTTs."""
        if self._jitter_pairs == 0:
            return None
        return self._jitter_sum / self._jitter_pairs

    def record_sent(self) -> None:
        self.sent += 1

    def record_reply(self, address: str, rtt_ms: float) -> None:
        self.received += 1
        self.addresses[address] += 1
        self.last = rtt_ms
        self.best = rtt_ms if self.best is None else min(self.best, rtt_ms)
        self.worst = rtt_ms if self.worst is None else max(self.worst, rtt_ms)
        self._sum += rtt_ms
        self._sum_sq += rtt_ms * rtt_ms
        if self._prev_rtt is not None:
            self._jitter_sum += abs(rtt_ms - self._prev_rtt)
            self._jitter_pairs += 1
        self._prev_rtt = rtt_ms
        self.history.append(rtt_ms)

    def record_timeout(self) -> None:
        self.history.append(None)

    def snapshot(self) -> dict:
        """Plain-data view handed to the GUI thread / exporters."""
        return {
            "ttl": self.ttl,
            "address": self.address,
            "all_addresses": list(self.addresses),
            "sent": self.sent,
            "received": self.received,
            "loss_pct": self.loss_pct,
            "last": self.last,
            "avg": self.avg,
            "best": self.best,
            "worst": self.worst,
            "stdev": self.stdev,
            "jitter": self.jitter,
            "history": list(self.history),
        }
