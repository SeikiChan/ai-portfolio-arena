"""Local Paper console orchestration; immutable rounds and complete prompt/response capture."""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .common import APP, DATA, ROOT, digest, external_reference, now, read, stamp, write
from . import packet_broker
from .models import INSTRUCTION_VERSION, REASONING_EFFORT, research_instruction, run_model, text_digest
from .paper_accounts import capture, load_credentials, model_portfolio, public_account

CONSOLE_DATA = ROOT / "reports" / "ai_paper_console"
# Style and objective only. Limits, evidence rules and output format live in code and in models.INSTRUCTION.
DEFAULT_PROMPT = """你的目标是在 6–12 个月尺度上做到资本增值，衡量口径是扣除成本后的累计收益与期间最大回撤的比值；不追求每周或每月领先，不以单轮浮动盈亏作为自我评价。

把你的判断力用在数据包里机器算不出来的地方：
1. 新闻说法与财务数字是否对得上。报道的改善在利润表里有没有对应科目变化；有分歧时，说明你相信哪一边以及为什么。
2. 同一事件对池内不同公司的含义差异（供应链上下游、竞争关系、成本端还是收入端）。
3. 你自己结论的反面证据。每个正权重持仓的失效条件，必须是这个数据包将来真的会提供的东西：价格特征、后续新闻、下一季财务。

动量和均线位置数据包已经算好了，复述这些数字不构成论点；它们只能作为"价格是否已经反映了你说的事"的旁证。

换手：持有理由不变就不要动仓位。每一次权重变化都要在 changes_vs_last 里指出触发它的新证据，无新证据的微调视为噪音。

集中度：在限额内宁可少而重，不要为了分散而持有你说不出理由的名字。现金是一个主动选择：找不到够清楚的机会时留现金，并说明你在等什么具体证据，不要用现金比例假装在做风险控制。

不确定性：confidence 要如实填。它会被和实际结果比对打分，长期高估会被看出来。"""


class ConsoleStore:
    def __init__(self, root=CONSOLE_DATA, legacy=DATA):
        self.root = Path(root)
        self.legacy = Path(legacy)
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = threading.RLock()
        self.operation = threading.Lock()
        self.active = None
        self.connection_error = None
        self.config = read(APP / "config.json")
        # Never claim a process killed during a prior round is still running.
        for path in (self.root / "rounds").glob("*/round.json"):
            data = read(path)
            if data.get("status") == "running":
                write(path.parent / "interruption.json", {"at": stamp(), "reason": "服务重新启动，上轮没有完成回执"})

    def prompts(self):
        path = self.root / "prompts.json"
        return read(path) if path.exists() else {"claude": DEFAULT_PROMPT, "gpt": DEFAULT_PROMPT}

    def save_prompts(self, prompts):
        if set(prompts) != {"claude", "gpt"} or any(not isinstance(p, str) or not 1 <= len(p.strip()) <= 12000 for p in prompts.values()):
            raise ValueError("两份 prompt 必须为 1–12,000 字符")
        with self.guard:
            write(self.root / "prompts.json", prompts)
        return prompts

    def _capture(self):
        credentials = load_credentials()
        pinned_path = self.root / "bindings.json"
        pinned = read(pinned_path) if pinned_path.exists() else {}
        directory = self.root / "accounts" / now().strftime("%Y%m%dT%H%M%S%fZ")
        snapshot, bindings = capture(directory, pinned, credentials)
        with self.guard:
            write(pinned_path, bindings)
            write(self.root / "latest_accounts.json", snapshot)
            self.connection_error = None
        return snapshot, credentials

    def refresh(self):
        if not self.operation.acquire(blocking=False):
            raise ValueError("已有研究或账户刷新正在运行，请稍后")
        try:
            snapshot, _ = self._capture()
            return {k: public_account(v) for k, v in snapshot["accounts"].items()}
        except (ValueError, OSError) as exc:
            with self.guard:
                self.connection_error = str(exc)
            raise
        finally:
            self.operation.release()

    def start(self, providers, prompts, packet_id=None):
        if not isinstance(providers, list) or not providers or len(set(providers)) != len(providers) or any(n not in {"claude", "gpt"} for n in providers):
            raise ValueError("请选择 Claude、GPT 或两者")
        self.save_prompts(prompts)
        if not self.operation.acquire(blocking=False):
            raise ValueError("已有研究或账户刷新正在运行，不能重复启动")
        try:
            run_id = now().strftime("%Y%m%dT%H%M%S%fZ")
            directory = self.root / "rounds" / run_id
            directory.mkdir(parents=True, exist_ok=False)
            mandates = {name: text_digest(prompts[name]) for name in providers}
            job = {"id": run_id, "providers": providers, "started_at": stamp(), "status": "running", "stage": "读取两个 Paper 账户",
                   "user_prompts": dict(prompts), "config": self.config, "environment": "alpaca_paper", "orders_submitted": False,
                   "instruction_version": INSTRUCTION_VERSION, "instruction_sha256": text_digest(research_instruction("alpaca_paper")),
                   "reasoning_effort": REASONING_EFFORT, "mandate_sha256": mandates,
                   # A round where the two mandates differ compares prompts, not models, and must not enter a model ranking.
                   "strict_comparison": len(providers) > 1 and len(set(mandates.values())) == 1,
                   # Set to join an existing shared round instead of freezing a new packet.
                   "packet_id": packet_id}
            write(directory / "round.json", job, exclusive=True)
            with self.guard:
                self.active = job
            threading.Thread(target=self._run, args=(directory, copy.deepcopy(job)), daemon=True).start()
            return run_id
        except Exception:
            self.operation.release()
            raise

    def _progress(self, directory, job):
        with self.guard:
            write(directory / "round.json", job)
            self.active = copy.deepcopy(job)

    def _last_decision(self, provider):
        # Never import the former synthetic book's thesis into the new Paper experiment.
        for item in self.records():
            if item["environment"] == "alpaca_paper" and item["provider"] == provider and item["status"] == "validated":
                return self.detail(item["id"])["result"].get("decision")
        return None

    def _run(self, directory, job):
        try:
            snapshot, credentials = self._capture()
            write(directory / "accounts_before.json", snapshot, exclusive=True)
            # Shared market feed, dedicated account state. Keys never enter model context.
            # The market-data pair is a neutral third Paper key, not either team's account: the shared
            # packet must come from one source, and the collector must not be one of the contestants.
            job["stage"] = "领取共同冻结证据包"
            self._progress(directory, job)
            # One freeze per round, read by every team. Never collect here: a round that collected its
            # own evidence cannot be paired with another team's round on the same market state.
            pointer = packet_broker.claim(self.config, directory, job.get("packet_id"),
                                          collector="paper_console")
            packet = read(directory / "packet.json")
            job["packet_id"] = pointer["packet_id"]
            job["packet_sha256"] = pointer["packet_sha256"]
            job["packet_frozen_at"] = pointer["frozen_at"]
            job["packet_age_seconds"] = pointer["age_seconds"]
            job["packet_hash"] = pointer["packet_sha256"]
            job["account_snapshot_id"] = snapshot["snapshot_id"]
            job["stage"] = "AI 独立研究中"
            self._progress(directory, job)
            portfolios = {}
            for name in job["providers"]:
                try:
                    portfolio = model_portfolio(snapshot["accounts"][name], self._last_decision(name))
                    if not set(portfolio["holdings"]).issubset(self.config["universe"]):
                        raise ValueError("账户存在当前股票池之外的持仓，需要先明确纳入研究范围")
                    portfolios[name] = portfolio
                except ValueError as exc:
                    write(directory / f"{name}_status.json", {"provider": name, "status": "failed", "error": str(exc),
                          "completed_at": stamp(), "portfolio_mode": "alpaca_paper"})
            with ThreadPoolExecutor(max_workers=2) as pool:
                tasks = {pool.submit(run_model, name, packet, portfolio, self.config, directory,
                                     job["user_prompts"][name], "alpaca_paper"): name for name, portfolio in portfolios.items()}
                for task in as_completed(tasks):
                    name = tasks[task]
                    result = task.result()
                    job.setdefault("model_statuses", {})[name] = result["status"]
                    self._progress(directory, job)
            statuses = {name: read(directory / f"{name}_status.json")["status"] for name in job["providers"]}
            job["model_statuses"] = statuses
            job["status"] = "completed" if all(s == "validated" for s in statuses.values()) else "partial_failure"
            job["stage"] = "研究完成" if job["status"] == "completed" else "部分研究失败，请查看原始回复"
        except Exception as exc:
            # No request exception text (which can include credentials) is exposed.
            job["status"] = "failed"
            job["stage"] = "运行中断"
            job["error"] = str(exc)[:300] if isinstance(exc, ValueError) else type(exc).__name__
        finally:
            try:
                job["completed_at"] = stamp()
                write(directory / "round.json", job)
                write(directory / "receipt.json", {"round_id": job["id"], "status": job["status"], "completed_at": job["completed_at"],
                      "snapshot_id": job["id"], "truth_reference": external_reference(), "orders_submitted": False,
                      "files": {p.name: digest(p) for p in directory.iterdir() if p.is_file()},
                      "source_code": {p.name: digest(p) for p in APP.glob("*.py")}}, exclusive=True)
            finally:
                with self.guard:
                    self.active = None
                self.operation.release()

    def records(self):
        records = []
        for environment, base in (("alpaca_paper", self.root / "rounds"), ("legacy_shadow", self.legacy / "runs")):
            for directory in sorted(base.glob("*"), reverse=True):
                if not directory.is_dir():
                    continue
                job_file = directory / "round.json"
                job = read(job_file) if job_file.exists() else {}
                for name in ("claude", "gpt"):
                    status_file = directory / f"{name}_status.json"
                    if not status_file.exists() and name not in job.get("providers", []):
                        continue
                    result = read(status_file) if status_file.exists() else {"status": job.get("status", "running")}
                    status = result["status"]
                    if (directory / "interruption.json").exists() and status == "running":
                        status = "interrupted"
                    if status == "running" and job.get("status") == "failed":
                        status = "failed"
                    records.append({"id": environment + ":" + directory.name + ":" + name, "run_id": directory.name,
                        "provider": name, "status": status, "environment": environment,
                        "at": result.get("completed_at") or result.get("started_at") or job.get("started_at") or directory.name,
                        "model": result.get("model_requested", self.config[name + "_model"]),
                        "summary": result.get("decision", {}).get("overview", result.get("error", job.get("stage", "等待输出")))[:160]})
        return sorted(records, key=lambda r: r["run_id"], reverse=True)[:160]

    def detail(self, identity):
        if not isinstance(identity, str):
            raise ValueError("Invalid record")
        index = {row["id"]: row for row in self.records()}
        if identity not in index:
            raise ValueError("未找到该研究记录")
        record = index[identity]
        base = self.root / "rounds" if record["environment"] == "alpaca_paper" else self.legacy / "runs"
        directory = base / record["run_id"]
        name = record["provider"]
        def text(suffix):
            path = directory / (name + suffix)
            return path.read_text(encoding="utf-8") if path.exists() else ""
        result_file = directory / f"{name}_status.json"
        result = read(result_file) if result_file.exists() else {}
        job = read(directory / "round.json") if (directory / "round.json").exists() else {}
        raw = text("_stdout.txt")
        answer = text("_response.txt")
        if not answer and raw and name == "claude":
            try:
                parsed = json.loads(raw)
                answer = parsed.get("result") or json.dumps(parsed.get("structured_output"), ensure_ascii=False, indent=2)
            except ValueError:
                answer = raw
        elif not answer and raw and name == "gpt":
            for line in raw.splitlines():
                try:
                    event = json.loads(line)
                    if event.get("item", {}).get("type") == "agent_message":
                        answer = event["item"].get("text", answer)
                except ValueError:
                    continue
        packet_file = directory / "packet.json"
        facts = {row["id"]: row["data"] for row in read(packet_file).get("evidence", []) if row.get("data")} if packet_file.exists() else {}
        return {**record, "user_prompt": job.get("user_prompts", {}).get(name, "旧观察轮次没有单独的用户指令字段；见完整发送正文。"),
                "full_prompt": text("_prompt.txt"), "response": answer, "raw_stdout": raw, "raw_stderr": text("_stderr.txt"),
                "result": result, "packet_hash": job.get("packet_hash"), "account_snapshot_id": job.get("account_snapshot_id"),
                "facts": facts, "instruction_version": job.get("instruction_version"), "mandate_sha256": job.get("mandate_sha256", {}).get(name),
                "strict_comparison": job.get("strict_comparison"),
                "orders_submitted": False, "prompt_note": "这是程序实际交给 CLI 的完整正文；CLI 自身内置系统提示不在本面板可见范围内。"}

    def state(self):
        with self.guard:
            path = self.root / "latest_accounts.json"
            snapshot = read(path) if path.exists() else {}
            return {"accounts": {k: public_account(v) for k, v in snapshot.get("accounts", {}).items()},
                    "account_snapshot_id": snapshot.get("snapshot_id"), "connection_error": self.connection_error,
                    "active": copy.deepcopy(self.active), "records": self.records(), "prompts": self.prompts(),
                    "rules": {k: self.config[k] for k in ("universe", "max_positions", "max_weight", "max_invested", "claude_model", "gpt_model")},
                    "research_instructions": research_instruction("alpaca_paper"), "execution_mode": "read_only_research"}
