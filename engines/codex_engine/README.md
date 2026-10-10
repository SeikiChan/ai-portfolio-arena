# codex_engine（待实现的槽位）

本目录是 Codex 独立设计的研究引擎位置。目前只有本说明，没有 `__init__.py`，所以
`engines.registry.loaded()` 会把它报告为 `declared slot, not implemented` —— 不是静默跳过。

## 要实现什么

读 `../../ENGINE_CONTRACT.md`，然后新建 `__init__.py` 并导出一个 `ENGINE` 对象：

```python
class CodexEngine:
    engine_id = "codex_engine"
    author = "Codex"
    version = "..."
    def instruction(self, portfolio_mode="alpaca_paper") -> str: ...
    def schema(self) -> dict: ...
    def decide(self, provider, packet, previous, mandate, directory, config=None) -> dict: ...

ENGINE = CodexEngine()
```

`engines/test_contract.py` 会自动对所有已实现引擎跑一致性检查，不需要为新引擎改测试。

## 请不要做的两件事

1. **不要从 `claude_engine` 导入任何东西，也不要照着它的指令文本改。** 这个槽位的全部价值在于它是**独立**写的。
   如果两版指令同源，交叉对照就测不出作者偏置了。请从 `ENGINE_CONTRACT.md` 和
   `docs/claude_gpt_portfolio_prompt_design_20261009.md` 的规格出发，不要从那份既有文本出发。
2. **不要只跑 GPT。** 契约第一条要求每个引擎都跑 Claude 和 GPT 两个模型。只填对角线会让
   模型效应、引擎效应和作者偏置永久不可分离。

## 这个槽位要回答的问题

claude_engine 的固定指令和委托书是 Claude 写的，而被评测的模型之一就是 Claude。这份 prompt 可能
是"Claude 形状"的 —— 不是故意的，但结果一样。两版独立撰写的指令 × 两个模型交叉跑，才能把这件事
从"顾虑"变成"量到或没量到的数字"。

如果两版结果一致，作者偏置这个顾虑就消掉了，这也是有价值的结论。
