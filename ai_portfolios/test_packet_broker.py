import json
from datetime import timedelta

import pytest

from ai_portfolios import packet_broker
from ai_portfolios.common import digest, now, read, write


def fake_collect(directory, config, keys=None):
    directory.mkdir(parents=True, exist_ok=True)
    packet = {"snapshot_id": directory.name, "frozen_at": now().isoformat(), "evidence": [], "features": [], "errors": []}
    write(directory / "packet.json", packet, exclusive=True)
    write(directory / "manifest.json", {"snapshot_id": directory.name}, exclusive=True)
    return packet


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(packet_broker, "collect", fake_collect)
    monkeypatch.setattr(packet_broker, "market_data_keys", lambda: {})
    return tmp_path / "store"


def test_empty_store_has_no_current_packet(store):
    assert packet_broker.current(store) is None


def test_freeze_then_everyone_reads_the_same_bytes(store, tmp_path):
    pointer = packet_broker.freeze({}, store=store, collector="team_a")
    first, second = tmp_path / "round_a", tmp_path / "round_b"
    a = packet_broker.claim({}, first, store=store)
    b = packet_broker.claim({}, second, store=store)
    assert a["packet_id"] == b["packet_id"] == pointer["packet_id"]
    assert (first / "packet.json").read_bytes() == (second / "packet.json").read_bytes()
    assert digest(first / "packet.json") == digest(second / "packet.json") == pointer["packet_sha256"]


def test_a_second_claim_does_not_collect_again(store, tmp_path):
    collected = []
    original = packet_broker.collect

    def counting(directory, config, keys=None):
        collected.append(directory.name)
        return original(directory, config, keys)

    packet_broker.collect = counting
    try:
        packet_broker.claim({}, tmp_path / "a", store=store)
        packet_broker.claim({}, tmp_path / "b", store=store)
        packet_broker.claim({}, tmp_path / "c", store=store)
    finally:
        packet_broker.collect = original
    assert len(collected) == 1


def test_claiming_a_named_round_joins_that_exact_packet(store, tmp_path):
    first = packet_broker.freeze({}, store=store)
    second = packet_broker.freeze({}, store=store)
    assert first["packet_id"] != second["packet_id"]
    joined = packet_broker.claim({}, tmp_path / "late", first["packet_id"], store=store)
    assert joined["packet_id"] == first["packet_id"]
    assert digest(tmp_path / "late" / "packet.json") == first["packet_sha256"]


def test_expired_packet_is_not_silently_reused(store, tmp_path):
    pointer = packet_broker.freeze({}, store=store)
    stale = dict(read(packet_broker.pointer_file(store)))
    stale["frozen_at"] = (now() - timedelta(hours=packet_broker.LIFETIME_HOURS + 1)).isoformat()
    write(packet_broker.pointer_file(store), stale)
    assert packet_broker.current(store)["expired"] is True
    with pytest.raises(ValueError, match="expired"):
        packet_broker.claim({}, tmp_path / "late", allow_freeze=False, store=store)
    fresh = packet_broker.claim({}, tmp_path / "fresh", store=store)
    assert fresh["packet_id"] != pointer["packet_id"]


def test_joining_team_cannot_be_forced_to_collect(store, tmp_path):
    with pytest.raises(ValueError, match="no packet in the store"):
        packet_broker.claim({}, tmp_path / "a", allow_freeze=False, store=store)


def test_tampering_with_a_frozen_packet_is_detected(store, tmp_path):
    pointer = packet_broker.freeze({}, store=store)
    target = packet_broker.rounds_root(store) / pointer["packet_id"] / "packet.json"
    target.write_text(json.dumps({"snapshot_id": "edited"}), encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches"):
        packet_broker.verify(pointer["packet_id"], store)
    with pytest.raises(ValueError, match="no longer matches"):
        packet_broker.claim({}, tmp_path / "a", pointer["packet_id"], store=store)


def test_a_frozen_packet_is_never_overwritten(store):
    pointer = packet_broker.freeze({}, store=store)
    directory = packet_broker.rounds_root(store) / pointer["packet_id"]
    with pytest.raises(FileExistsError):
        write(directory / "store_manifest.json", {"packet_id": "other"}, exclusive=True)


def test_matched_pair_requires_the_same_packet():
    a = {"packet_id": "p1", "packet_sha256": "h1", "engine_id": "claude_engine", "providers": ["claude", "gpt"]}
    b = {"packet_id": "p1", "packet_sha256": "h1", "engine_id": "codex_engine", "providers": ["claude", "gpt"]}
    paired = packet_broker.check_pair(a, b)
    assert paired["engines"] == ["claude_engine", "codex_engine"]
    assert paired["providers"] == ["claude", "gpt"]


def test_rounds_on_different_packets_are_never_comparable():
    a = {"packet_id": "p1", "packet_sha256": "h1"}
    with pytest.raises(ValueError, match="Different packets"):
        packet_broker.check_pair(a, {"packet_id": "p2", "packet_sha256": "h2"})


def test_same_id_different_bytes_is_reported_as_tampering():
    with pytest.raises(ValueError, match="tampered"):
        packet_broker.check_pair({"packet_id": "p1", "packet_sha256": "h1"},
                                 {"packet_id": "p1", "packet_sha256": "h2"})


@pytest.mark.parametrize("job", [{}, {"packet_id": "p1"}, {"packet_sha256": "h1"}, {"packet_id": "", "packet_sha256": "h"}])
def test_a_round_without_packet_identity_cannot_be_paired(job):
    with pytest.raises(ValueError, match="must record packet_id"):
        packet_broker.check_pair(job, {"packet_id": "p1", "packet_sha256": "h1"})


def test_store_location_is_overridable_by_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ARENA_PACKET_STORE", str(tmp_path / "elsewhere"))
    import importlib

    reloaded = importlib.reload(packet_broker)
    try:
        assert reloaded.STORE == tmp_path / "elsewhere"
    finally:
        monkeypatch.delenv("AI_ARENA_PACKET_STORE")
        importlib.reload(packet_broker)
