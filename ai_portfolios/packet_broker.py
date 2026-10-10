"""Neutral packet store. One freeze per round; every team reads the same bytes.

Two independent teams can only be compared if they saw identical evidence. If each team collects its
own data, the model comparison and the engine comparison both become void, because any difference in
their answers could come from a different price or a different headline rather than from judgement.

The store lives OUTSIDE every team's workspace, so sharing it does not breach the rule that no team
imports another team's code or state: a frozen packet is a shared INPUT, not another team's state.
Reading a directory is not importing code, so a team needs nothing from this module to consume a
packet - `current.json` plus `json.load` and `sha256` is the whole client. See PACKET_STORE.md.

Writes here are append-only and exclusive. A frozen packet is never edited, never re-serialised and
never regenerated under the same id: bytes are copied, so a round's local copy keeps the same hash.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from .common import APP, digest, env_values, now, parse_time, read, stamp, write
from .data import collect

STORE = Path(os.environ.get("AI_ARENA_PACKET_STORE") or Path.home() / "ai-arena-packets")
# assumption: matches the 24h precedent already used for plan lifetime. A team joining a round later
# uses the same frozen bytes; simultaneity was never required, identical input is.
LIFETIME_HOURS = 24


def rounds_root(store=None):
    return (Path(store) if store else STORE) / "rounds"


def pointer_file(store=None):
    return (Path(store) if store else STORE) / "current.json"


@contextmanager
def freeze_lock(store=None):
    root = Path(store) if store else STORE
    root.mkdir(parents=True, exist_ok=True)
    with (root / "freeze.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Another freeze is in progress; never collect twice for one round") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def describe(pointer, store=None):
    """Add observed age to a stored pointer. Expiry is computed, never trusted from the file."""
    frozen = parse_time(pointer["frozen_at"])
    age = (now() - frozen).total_seconds()
    return {**pointer, "age_seconds": round(age, 3), "expired": age > LIFETIME_HOURS * 3600,
            "directory": str(rounds_root(store) / pointer["packet_id"])}


def current(store=None):
    path = pointer_file(store)
    if not path.exists():
        return None
    return describe(read(path), store)


def verify(packet_id, store=None):
    directory = rounds_root(store) / packet_id
    packet = directory / "packet.json"
    if not packet.exists():
        raise ValueError(f"Packet {packet_id} is not in the store")
    recorded = read(directory / "store_manifest.json")
    actual = digest(packet)
    if actual != recorded["packet_sha256"]:
        raise ValueError(f"Packet {packet_id} no longer matches its recorded hash")
    return {"packet_id": packet_id, "packet_sha256": actual, "frozen_at": recorded["frozen_at"]}


def freeze(config, keys=None, store=None, collector="unknown"):
    """Collect exactly one packet into the store and make it current. Fails if a freeze is running."""
    with freeze_lock(store):
        packet_id = now().strftime("%Y%m%dT%H%M%S%fZ")
        directory = rounds_root(store) / packet_id
        directory.mkdir(parents=True, exist_ok=False)
        collect(directory, config, keys=keys if keys is not None else env_values())
        pointer = {"packet_id": packet_id, "frozen_at": read(directory / "packet.json")["frozen_at"],
                   "packet_sha256": digest(directory / "packet.json"), "collector": collector,
                   "lifetime_hours": LIFETIME_HOURS, "created_at": stamp(),
                   "source_code": {p.name: digest(p) for p in APP.glob("*.py")},
                   "classification": {"market_data": "observed", "price_features": "derived"}}
        write(directory / "store_manifest.json", pointer, exclusive=True)
        write(pointer_file(store), pointer)
        return describe(pointer, store)


def claim(config, directory, packet_id=None, allow_freeze=True, keys=None, store=None, collector="unknown"):
    """Hand a round the shared packet, copying bytes so the round's hash equals the store's.

    `packet_id` joins a specific round, which is what a matched pair requires. Without it the current
    packet is reused while it is fresh, and a new one is frozen only when allowed.
    """
    if packet_id:
        pointer = read(rounds_root(store) / packet_id / "store_manifest.json")
        pointer = describe(pointer, store)
    else:
        pointer = current(store)
        if pointer is None or pointer["expired"]:
            if not allow_freeze:
                reason = "no packet in the store" if pointer is None else "the current packet has expired"
                raise ValueError(f"Cannot join a shared round: {reason}, and freezing is not allowed here")
            pointer = freeze(config, keys=keys, store=store, collector=collector)
    verify(pointer["packet_id"], store)
    source = rounds_root(store) / pointer["packet_id"]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("packet.json", "store_manifest.json", "manifest.json"):
        if (source / name).exists():
            # Copy bytes: re-serialising JSON would change the hash the contract checks.
            shutil.copyfile(source / name, directory / ("packet.json" if name == "packet.json" else name))
    if digest(directory / "packet.json") != pointer["packet_sha256"]:
        raise ValueError("Copied packet does not match the store hash")
    return pointer


def check_pair(first, second):
    """Assert two rounds are a matched pair. Rounds on different packets are never comparable."""
    for job in (first, second):
        if not isinstance(job, dict) or not job.get("packet_id") or not job.get("packet_sha256"):
            raise ValueError("A round must record packet_id and packet_sha256 to be paired")
    if first["packet_id"] != second["packet_id"]:
        raise ValueError(f"Different packets: {first['packet_id']} vs {second['packet_id']}")
    if first["packet_sha256"] != second["packet_sha256"]:
        raise ValueError("Same packet id with different bytes; the store was tampered with")
    return {"packet_id": first["packet_id"], "packet_sha256": first["packet_sha256"],
            "engines": sorted({first.get("engine_id"), second.get("engine_id")} - {None}),
            "providers": sorted(set(first.get("providers", [])) | set(second.get("providers", [])))}


def main():
    parser = argparse.ArgumentParser(description="Neutral shared packet store.")
    parser.add_argument("action", choices=("current", "freeze", "verify", "claim"))
    parser.add_argument("--packet-id")
    parser.add_argument("--into", help="claim: round directory to copy the packet into")
    parser.add_argument("--store", help="override the store location (default: $AI_ARENA_PACKET_STORE)")
    parser.add_argument("--collector", default="cli")
    arguments = parser.parse_args()
    config = read(APP / "config.json")
    if arguments.action == "current":
        pointer = current(arguments.store)
        print(json.dumps(pointer or {"status": "empty", "store": str(Path(arguments.store or STORE))},
                         ensure_ascii=False, indent=2))
        raise SystemExit(0 if pointer and not pointer["expired"] else 1)
    if arguments.action == "verify":
        print(json.dumps(verify(arguments.packet_id or (current(arguments.store) or {}).get("packet_id"),
                                arguments.store), ensure_ascii=False, indent=2))
        return
    if arguments.action == "freeze":
        print(json.dumps(freeze(config, store=arguments.store, collector=arguments.collector),
                         ensure_ascii=False, indent=2))
        return
    if not arguments.into:
        parser.error("claim needs --into")
    print(json.dumps(claim(config, arguments.into, arguments.packet_id, store=arguments.store,
                           collector=arguments.collector), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
