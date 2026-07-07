import math

from mtr_advanced.core.stats import HopStats


def test_initial_state():
    hop = HopStats(ttl=3)
    assert hop.loss_pct == 0.0
    assert hop.avg is None
    assert hop.stdev is None
    assert hop.jitter is None
    assert hop.address is None


def test_loss_percentage():
    hop = HopStats(ttl=1)
    for _ in range(4):
        hop.record_sent()
    hop.record_reply("10.0.0.1", 10.0)
    hop.record_timeout()
    hop.record_reply("10.0.0.1", 20.0)
    hop.record_timeout()
    assert hop.sent == 4
    assert hop.received == 2
    assert hop.loss_pct == 50.0


def test_rtt_aggregates():
    hop = HopStats(ttl=1)
    rtts = [10.0, 20.0, 30.0, 40.0]
    for rtt in rtts:
        hop.record_sent()
        hop.record_reply("192.0.2.1", rtt)
    assert hop.best == 10.0
    assert hop.worst == 40.0
    assert hop.last == 40.0
    assert hop.avg == 25.0
    # Sample stdev of [10,20,30,40]
    assert math.isclose(hop.stdev, 12.909944, rel_tol=1e-6)
    # Jitter: mean of |20-10|, |30-20|, |40-30| = 10
    assert hop.jitter == 10.0


def test_multiple_addresses_prefers_most_common():
    hop = HopStats(ttl=5)
    for _ in range(3):
        hop.record_sent()
        hop.record_reply("198.51.100.1", 5.0)
    hop.record_sent()
    hop.record_reply("198.51.100.2", 5.0)
    assert hop.address == "198.51.100.1"
    assert set(hop.snapshot()["all_addresses"]) == {
        "198.51.100.1", "198.51.100.2"}


def test_history_tracks_losses():
    hop = HopStats(ttl=2)
    hop.record_sent()
    hop.record_reply("10.0.0.1", 12.5)
    hop.record_sent()
    hop.record_timeout()
    assert list(hop.history) == [12.5, None]


def test_snapshot_is_plain_data():
    hop = HopStats(ttl=7)
    hop.record_sent()
    hop.record_reply("10.1.1.1", 33.3)
    snap = hop.snapshot()
    assert snap["ttl"] == 7
    assert snap["address"] == "10.1.1.1"
    assert snap["loss_pct"] == 0.0
    assert snap["history"] == [33.3]
