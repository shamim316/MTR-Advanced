"""Engine tests using a stubbed probe function (no network needed)."""

import time
from unittest import mock

from mtr_advanced.core.probe import ProbeResult, ProbeStatus
from mtr_advanced.core.tracer import MtrEngine, TraceConfig


def fake_probe_factory(path):
    """path: dict ttl -> (status, responder, rtt)."""
    def fake_probe(dest_ip, ttl, timeout_ms=1000, size=56):
        status, responder, rtt = path.get(
            ttl, (ProbeStatus.TIMEOUT, None, None))
        return ProbeResult(status, responder, rtt)
    return fake_probe


def run_engine_rounds(path, rounds=2, max_hops=10):
    snapshots = []
    done = []

    def on_snapshot(snap):
        snapshots.append(snap)
        if len(snapshots) >= rounds:
            done.append(True)

    config = TraceConfig(target="203.0.113.9", interval_s=0.01,
                         timeout_ms=50, max_hops=max_hops)
    engine = MtrEngine(config, on_snapshot=on_snapshot)
    with mock.patch("mtr_advanced.core.tracer.probe",
                    side_effect=fake_probe_factory(path)), \
         mock.patch("mtr_advanced.core.tracer.resolve_target",
                    return_value=("203.0.113.9", 2)):
        engine.start()
        deadline = time.time() + 5
        while not done and time.time() < deadline:
            time.sleep(0.01)
        engine.stop()
    assert snapshots, "engine produced no snapshots"
    return snapshots


def test_discovers_path_and_stops_at_destination():
    path = {
        1: (ProbeStatus.TTL_EXPIRED, "10.0.0.1", 1.0),
        2: (ProbeStatus.TTL_EXPIRED, "10.0.0.2", 5.0),
        3: (ProbeStatus.REPLY, "203.0.113.9", 12.0),
    }
    snapshots = run_engine_rounds(path, rounds=2)
    final = snapshots[-1]
    assert final["dest_reached"] is True
    assert [h["ttl"] for h in final["hops"]] == [1, 2, 3]
    assert final["hops"][-1]["address"] == "203.0.113.9"
    assert final["hops"][0]["loss_pct"] == 0.0


def test_silent_hop_shows_full_loss():
    path = {
        1: (ProbeStatus.TTL_EXPIRED, "10.0.0.1", 1.0),
        # ttl 2 never answers
        3: (ProbeStatus.REPLY, "203.0.113.9", 12.0),
    }
    final = run_engine_rounds(path, rounds=2)[-1]
    hop2 = final["hops"][1]
    assert hop2["address"] is None
    assert hop2["loss_pct"] == 100.0


def test_unreached_destination_trims_trailing_silence():
    path = {
        1: (ProbeStatus.TTL_EXPIRED, "10.0.0.1", 1.0),
        2: (ProbeStatus.TTL_EXPIRED, "10.0.0.2", 2.0),
        # everything beyond times out; destination never reached
    }
    final = run_engine_rounds(path, rounds=2, max_hops=20)[-1]
    assert final["dest_reached"] is False
    # Trailing dead hops collapse to at most one visible silent row
    silent_tail = [h for h in final["hops"] if h["address"] is None]
    assert len(silent_tail) <= 2
    assert len(final["hops"]) < 20


def test_resolution_failure_reports_error():
    errors = []
    config = TraceConfig(target="definitely-not-a-real-host.invalid")
    engine = MtrEngine(config, on_snapshot=lambda s: None,
                       on_error=errors.append)
    with mock.patch("mtr_advanced.core.tracer.resolve_target",
                    side_effect=OSError("no such host")):
        engine.start()
    assert errors and "Cannot resolve" in errors[0]
    assert not engine.running
