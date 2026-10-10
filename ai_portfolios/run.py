"""Run: python -m ai_portfolios.run [--refresh-only]. No trading API endpoints."""
from __future__ import annotations

import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from .book import initial_book, record_equity, settle
from .common import APP, DATA, digest, external_reference, now, parse_time, read, stamp, write
from .dashboard import render
from .data import Collector, collect
from .models import run_model


@contextmanager
def exclusive_run():
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "run.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Another portfolio run is already active") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def load_state(config):
    path = DATA / "state.json"
    if path.exists():
        state = read(path)
        if state["config"] != config:
            raise ValueError("Configuration changed: use a separately named experiment instead of rewriting the comparison")
        return state
    return {"created_at": stamp(), "config": config, "books": {name: initial_book(config["initial_cash"])
            for name in ("claude", "gpt", *config["benchmarks"])}, "model_results": {}, "marks": {}, "settlement": {}}


def update_marks(state, packet, at):
    for name, book in list(state["books"].items()):
        updated, status = settle(book, packet.get("quotes", {}), at, state["config"])
        state["books"][name] = updated
        state["settlement"][name] = status
        state["marks"][name] = record_equity(updated, packet.get("quotes", {}), at, state["config"])


def save(state):
    write(DATA / "state.json", state)
    return render(state)


def refresh(state, directory):
    c = Collector(directory)
    symbols = state["config"]["universe"] + state["config"]["benchmarks"]
    response = c.get("quotes", "/v2/stocks/quotes/latest", {"symbols": ",".join(symbols), "feed": state["config"]["quote_feed"]})
    packet = {"snapshot_id": directory.name, "quotes": (response or {}).get("quotes", {}), "records": c.records, "errors": c.errors,
              "frozen_at": stamp(), "truth_reference": external_reference(), "source_code": {p.name: digest(p) for p in APP.glob("*.py")}}
    write(directory / "execution_snapshot.json", packet, exclusive=True)
    update_marks(state, packet, now())
    state["last_execution_snapshot"] = directory.name
    write(directory / "state_after.json", state, exclusive=True)
    save(state)


def retry(state, directory, name):
    original_id = state["last_packet_summary"]["snapshot_id"]
    original = DATA / "runs" / original_id
    packet = read(original / "packet.json")
    if digest(original / "packet.json") != read(original / "manifest.json")["packet_sha256"]:
        raise ValueError("Frozen evidence changed; refusing retry")
    if state["model_results"].get(name, {}).get("status") != "failed":
        raise ValueError("Retry is only for failed runs, never for choosing a preferred answer")
    if (now() - parse_time(packet["frozen_at"])).total_seconds() > 86400:
        raise ValueError("Evidence older than 24 hours; start a new shared research run")
    config = state["config"]
    result = run_model(name, packet, copy.deepcopy(state["books"][name]), config, directory)
    state["model_results"][name] = result
    if result["status"] == "validated":
        book = state["books"][name]
        book["last_decision"] = result["decision"]
        book["pending"] = {"id": directory.name + ":" + name, "decision": result["decision"], "completed_at": result["completed_at"]}
    write(directory / "retry_receipt.json", {"source_snapshot": original_id, "source_packet_sha256": digest(original / "packet.json"),
          "model": name, "status": result["status"], "completed_at": stamp(), "orders_submitted": False,
          "source_code": {p.name: digest(p) for p in APP.glob("*.py")}}, exclusive=True)
    write(directory / "state_after.json", state, exclusive=True)
    save(state)
    print(name + ": " + result["status"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-only", action="store_true", help="Only mark/settle from new quotes, no model calls")
    parser.add_argument("--retry", choices=("claude", "gpt"), help="Retry a failed model on the same frozen packet, within 24 hours")
    args = parser.parse_args()
    config = read(APP / "config.json")
    with exclusive_run():
        state = load_state(config)
        run_id = now().strftime("%Y%m%dT%H%M%S%fZ")
        directory = DATA / "runs" / run_id
        directory.mkdir(parents=True, exist_ok=False)
        write(directory / "state_before.json", state, exclusive=True)
        if args.retry:
            if args.refresh_only:
                raise ValueError("Choose retry OR refresh")
            retry(state, directory, args.retry)
            return
        if args.refresh_only:
            refresh(state, directory)
            print(json.dumps({"dashboard": str(DATA / "dashboard.html"), "settlement": state["settlement"]}, ensure_ascii=False))
            return
        print("Collecting shared public evidence...", flush=True)
        packet = collect(directory, config)
        update_marks(state, packet, now())
        state["last_packet_summary"] = {k: packet[k] for k in ("snapshot_id", "frozen_at", "truth_reference", "evidence", "features", "errors")}
        # Both providers receive the same market packet and their own independent history.
        for name in ("claude", "gpt"):
            state["model_results"][name] = {"status": "running", "model_requested": config[name + "_model"], "started_at": stamp()}
        save(state)
        print(f"Frozen {len(packet['evidence'])} evidence records; launching two independent decisions.", flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(run_model, name, packet, copy.deepcopy(state["books"][name]), config, directory): name for name in ("claude", "gpt")}
            for future in as_completed(futures):
                name = futures[future]
                result = future.result()
                state["model_results"][name] = result
                book = state["books"][name]
                if result["status"] == "validated":
                    if book["pending"]:
                        book["events"].append({"type": "plan_superseded", "id": book["pending"]["id"], "at": stamp()})
                    book["last_decision"] = result["decision"]
                    book["pending"] = {"id": run_id + ":" + name, "decision": result["decision"], "completed_at": result["completed_at"]}
                save(state)
                print(name + ": " + result["status"] + (" " + result.get("error", "") if result["status"] == "failed" else ""), flush=True)
        # Benchmarks begin no earlier than BOTH first decisions complete; no backdated fills.
        for symbol in config["benchmarks"]:
            book = state["books"][symbol]
            if not book["holdings"] and not book["pending"]:
                book["pending"] = {"id": run_id + ":" + symbol, "completed_at": stamp(),
                                   "decision": {"positions": [{"symbol": symbol, "target_weight": config["max_invested"]}]}}
        write(directory / "state_after.json", state, exclusive=True)
        write(directory / "run_receipt.json", {"run_id": run_id, "completed_at": stamp(), "input_sha256": digest(directory / "packet.json"),
              "model_statuses": {k: state["model_results"][k]["status"] for k in ("claude", "gpt")},
              "output_hashes": {k: digest(directory / f"{k}_status.json") for k in ("claude", "gpt")},
              "state_sha256": digest(directory / "state_after.json"), "orders_submitted": False}, exclusive=True)
        save(state)
        print("Dashboard: " + str(DATA / "dashboard.html"), flush=True)


if __name__ == "__main__":
    main()
