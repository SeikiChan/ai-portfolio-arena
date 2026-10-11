"""Run one independent trading team against its own Alpaca Paper account."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ai_portfolios import packet_broker
from ai_portfolios.common import APP, ROOT, digest, now, read, stamp, write
from ai_portfolios.credentials import paper_credentials
from ai_portfolios.paper_accounts import model_portfolio, read_one

from . import contract, registry, research

ENGINE_PROVIDER = {"claude_engine": "claude", "codex_engine": "gpt"}
DEFAULT_MANDATE = """以 6–12 个月为复核尺度，优先做资本增值。请结合新闻、财务数据与公司间影响，说明反面证据和数据缺口。持有理由没有新变化时可以不调整；现金应说明等待的具体证据。"""


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_mandate(engine_id):
    engine = registry.get(engine_id)
    loader = getattr(engine, "default_mandate", None)
    return loader() if callable(loader) else DEFAULT_MANDATE


@contextmanager
def _engine_lock(path):
    """One active process per account/engine; OS releases the lock after a crash."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("该团队已有一轮正在运行，拒绝并发访问账户") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _latest_decision(rounds_root, provider, experiment_id=None):
    for directory in sorted((p for p in Path(rounds_root).glob("*") if p.is_dir()), reverse=True):
        path = directory / f"{provider}_status.json"
        if not path.exists():
            continue
        if experiment_id is not None:
            job_path = directory / "round.json"
            if not job_path.exists() or read(job_path).get("experiment_id") != experiment_id:
                continue
        status = read(path)
        if status.get("status") == "validated" and isinstance(status.get("decision"), dict):
            return status["decision"]
    return None


def _bind_account(engine_id, account_id):
    path = ROOT / "reports" / "engines" / engine_id / "account_binding.json"
    if path.exists():
        binding = read(path)
        if binding.get("account_id") != account_id:
            raise ValueError("Paper 账户身份与该团队历史绑定不符；必须另建实验，拒绝沿用旧组合")
        return path
    write(path, {"engine_id": engine_id, "account_id": account_id, "bound_at": stamp(),
                 "classification": "observed: Alpaca Paper account identity"}, exclusive=True)
    return path


def run(engine_id, mandate=None, packet_id=None, config=None, output_root=None, submit_orders=True):
    if engine_id not in ENGINE_PROVIDER:
        raise ValueError("只有 claude_engine 和 codex_engine 可以运行交易轮次")
    if mandate is None:
        mandate = default_mandate(engine_id)
    engine = registry.get(engine_id)
    provider = ENGINE_PROVIDER[engine_id]
    config = config or read(APP / "config.json")
    research_enabled = research.enabled(engine_id, config)
    experiment_id = research.identity(engine, config, mandate)
    rounds_root = Path(output_root) if output_root else contract.result_root(engine_id)
    rounds_root.mkdir(parents=True, exist_ok=True)
    round_id = now().strftime("%Y%m%dT%H%M%S%fZ")
    directory = rounds_root / round_id

    with _engine_lock(rounds_root.parent / "run.lock"):
        with tempfile.TemporaryDirectory(prefix="engine_input_") as staging:
            staged = Path(staging)
            pointer = packet_broker.claim(config, staged, packet_id=packet_id, allow_freeze=False,
                                          collector=engine_id)
            for prior_round in rounds_root.glob("*/round.json"):
                if read(prior_round).get("packet_id") == pointer["packet_id"]:
                    raise ValueError(f"该团队已经消费过 packet {pointer['packet_id']}；禁止重跑择优")
            credentials = paper_credentials(only=provider)[provider]
            account_dir = staged / "account_snapshot"
            account = read_one(provider, credentials, account_dir)
            if account.get("status") != "connected":
                raise ValueError(f"{provider} Paper 账户读取不完整，拒绝生成交易决定")
            account_id = account.get("account", {}).get("id")
            if not account_id:
                raise ValueError("Alpaca 未返回账户身份，拒绝继续")
            _bind_account(engine_id, account_id)

            directory.mkdir(parents=True, exist_ok=False)
            for name in ("packet.json", "store_manifest.json", "manifest.json"):
                source = staged / name
                if source.exists():
                    shutil.copyfile(source, directory / name)
            shutil.copytree(account_dir, directory / "account_snapshot")

        account_files = {path.name: digest(path) for path in (directory / "account_snapshot").iterdir()
                         if path.is_file()}

        packet = read(directory / "packet.json")
        if digest(directory / "packet.json") != pointer["packet_sha256"]:
            raise ValueError("Claimed packet bytes do not match the shared store")
        previous_decision = _latest_decision(rounds_root, provider, experiment_id)
        previous = model_portfolio(account, previous_decision)
        mandates = {provider: _sha(mandate)}
        instruction_hash = _sha(engine.instruction("alpaca_paper"))
        job = {"id": round_id, "engine_id": engine.engine_id, "engine_version": engine.version,
               "experiment_id": experiment_id, "research_enabled": research_enabled,
               "engine_author": engine.author, "providers": [provider], "environment": "alpaca_paper",
               "account_snapshot_id": account.get("fetched_at"),
               "account_snapshot": {"provider": provider,
                   "account_id_sha256": _sha(account_id), "files": account_files,
                   "classification": "observed: own Alpaca Paper account snapshot"},
               "packet_id": pointer["packet_id"], "packet_sha256": pointer["packet_sha256"],
               "packet_frozen_at": pointer["frozen_at"], "packet_age_seconds": pointer["age_seconds"],
               "instruction_sha256": instruction_hash, "mandate_sha256": mandates,
               "strict_comparison": False, "reasoning_effort": "medium", "orders_submitted": False,
               "status": "running", "started_at": stamp(), "completed_at": None,
               "user_prompts": {provider: mandate},
               "accounting": "observed: own Alpaca Paper account snapshot; simulated orders are not live-money trades"}
        write(directory / "round.json", job, exclusive=True)

        try:
            decision_packet = packet
            if research_enabled:
                # A research failure still leaves a sealable failed round; no portfolio is fabricated.
                (directory / f"{provider}_prompt.txt").write_text(
                    "Final decision not invoked yet; see research planning artifacts.", encoding="utf-8")
                decision_packet = research.run(provider, packet, config, directory)
                job["decision_packet_sha256"] = digest(directory / "decision_packet.json")
                job["research_completed_at"] = decision_packet["frozen_at"]
            result = engine.decide(provider, decision_packet, previous, mandate, directory, config)
            if research_enabled:
                result["experiment_id"] = experiment_id
                result["decision_packet_sha256"] = job["decision_packet_sha256"]
            result.setdefault("provider", provider)
            result.setdefault("engine_id", engine.engine_id)
            result.setdefault("engine_version", engine.version)
            result.setdefault("engine_author", engine.author)
            result["orders_submitted"] = False
            write(directory / f"{provider}_status.json", result)
            statuses = {provider: result.get("status", "failed")}
        except Exception as exc:
            statuses = {provider: "failed"}
            write(directory / f"{provider}_status.json", {"provider": provider, "status": "failed",
                  "failure_kind": getattr(exc, "failure_kind", "round_failure"),
                  "error": f"{type(exc).__name__}: {str(exc)[:250]}", "completed_at": stamp(),
                  "orders_submitted": False})

        job["model_statuses"] = statuses
        job["status"] = "completed" if statuses[provider] == "validated" else "failed"
        job["completed_at"] = stamp()
        write(directory / "round.json", job)
        receipt = {"round_id": round_id, "engine_id": engine.engine_id, "packet_id": pointer["packet_id"],
                   "packet_sha256": pointer["packet_sha256"], "status": job["status"],
                   "completed_at": job["completed_at"], "orders_submitted": False,
                   "files": {p.name: digest(p) for p in directory.iterdir() if p.is_file() and p.name != "receipt.json"},
                   "source_code": {str(p.relative_to(ROOT)): digest(p) for p in ROOT.glob("**/*.py")
                                   if ".venv" not in p.parts and ".git" not in p.parts and "__pycache__" not in p.parts}}
        write(directory / "receipt.json", receipt, exclusive=True)
        contract.validate_round(directory)

        execution_result = None
        if job["status"] == "completed":
            from ai_portfolios.execution import execute
            try:
                execution_result = execute(provider, directory, config, submit_orders=submit_orders)
            except Exception as exc:
                execution_result = {"round_id": round_id, "engine_id": engine_id, "provider": provider,
                                    "packet_id": pointer["packet_id"],
                                    "mode": "submitted" if submit_orders else "dry_run",
                                    "orders_submitted": False,
                                    "execution_error": f"{type(exc).__name__}: {str(exc)[:250]}",
                                    "completed_at": stamp()}
                write(rounds_root.parent / "executions" / round_id / "execution.json",
                      execution_result, exclusive=True)
        from .publish import DEFAULT_EXPORTS, TEAM_IDS, build
        export = build(engine_id, TEAM_IDS[engine_id])
        write(DEFAULT_EXPORTS[engine_id], export)
        return directory, job, execution_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=sorted(ENGINE_PROVIDER))
    parser.add_argument("--mandate-file", help="UTF-8 mandate text")
    parser.add_argument("--packet-id", help="join this exact shared packet; never collect inside an engine")
    parser.add_argument("--no-submit", action="store_true", help="build a dry-run plan only")
    args = parser.parse_args()
    mandate = Path(args.mandate_file).read_text(encoding="utf-8") if args.mandate_file else default_mandate(args.engine)
    directory, job, execution_result = run(args.engine, mandate, args.packet_id,
                                           submit_orders=not args.no_submit)
    print(json.dumps({"round_id": job["id"], "engine_id": job["engine_id"],
                      "provider": ENGINE_PROVIDER[args.engine], "packet_id": job["packet_id"],
                      "status": job["status"], "execution": execution_result,
                      "directory": str(directory)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
