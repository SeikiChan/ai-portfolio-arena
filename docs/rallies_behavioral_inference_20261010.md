# Rallies Arena 行为推断：从公开数据反推运行规则

日期：2026-10-10｜性质：观察研究｜来源：`https://rallies.ai/arena/<model>` 页面自身调用的公开端点 `GET /api/get-arena-data-v4?limit=N&feed_model=<model>`（无需认证，服务的就是页面上公开展示的内容）

## 0. 方法与边界

**做了什么**：读他们主动公开的页面数据，对五个模型的决策流做统计，用**跨模型家族的结构一致性**作为判别器：

> 五个不同模型家族共有的结构是 prompt 造成的；只在某一个模型上出现的是那个模型自己的文风。

**没有做什么**：没有构造输入去诱导他们的模型泄露系统提示，没有探测未公开的私有端点，没有绕过任何认证。他们的 [API 文档](https://rallies.ai/api-docs/aifunds)明确声明不暴露 prompt / model version / tool inventory / schedule / configuration —— prompt 原文拿不到，这一点没有变。

**版权**：本文只报告统计量、字段名和标签频次，不复制他们的 reasoning 正文。

分类：端点返回的字段与数值为 `observed`；由其计算的权重、持有期分布、长度分布为 `derived`；"prompt 里写了什么"为 `inference`，置信度逐条标注；他们的真实 prompt 文本为 `unknown`。

---

## 1. observed：五个参赛模型的实际状态

| | 模型（他们自己的字段） | 起始日 | 收益 | 交易数 | 胜率 | 最大回撤 | 持仓数 | 现金 | 最大单仓 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| Claude | `openrouter/anthropic/claude-opus-4.6` | 2025-11-25 | +28.4% | 44 | 72.7% | 8.7% | 11 | 14.6% | EME 12.3% |
| GPT | `openrouter/openai/gpt-6-astra` | **2026-03-06** | +77.1% | 27 | 96.3% | 16.5% | 10 | 0.5% | GOOGL 17.9% |
| Gemini | `openrouter/google/gemini-3-flash-preview` | 2025-11-25 | +22.0% | 43 | 67.4% | 9.3% | 7 | 0.9% | NEE 31.8% |
| Grok | `openrouter/x-ai/grok-4.6` | 2025-11-25 | +71.5% | 25 | 88.0% | 14.3% | 5 | 1.4% | **MU 56.4%** |
| DeepSeek | `openrouter/deepseek/deepseek-chat-v3.1` | 2025-11-25 | +0.9% | 86 | 70.9% | 15.5% | 16 | 4.2% | GGG 17.7% |

初始资金 100,000，全部 `position_type: long` / `asset_type: stock`。基准序列是 `performance_history: {snapshot_time, capital, spy_price}` —— 对标 SPY。

**模型接入走 OpenRouter。** 没有特殊 API 通道，用的是任何人都能调的同款模型。

## 2. observed：调度与换手

- **100 条决策，`utc_hour` 全部等于 21**。星期分布 18/20/21/22/19，只有周一到周五。→ **每交易日收盘后固定一次，没有盘中触发。**
- **决策动作分布**（各取最近 100 条）：

| | hold | buy | sell |
|---|---:|---:|---:|
| Claude | **74** | 12 | 14 |
| GPT | 66 | 17 | 17 |
| Gemini | 61 | 21 | 18 |
| Grok | 60 | 37 | 3 |
| DeepSeek | 48 | 23 | 29 |

  `hold` 的 `executed` 全为 0、`quantity` 为 null —— 它是一个**一等公民动作**，不产生订单。Claude 最近 100 个交易日里只有 26 天真的下了单。
- `last_trade_date` 与 `last_decision_date` 的差：Claude 2026-09-21 vs 2026-10-09 —— **连续 18 个交易日每天复核、零交易。**
- **实际持有期**（`days_held`）：Claude 10 / 38 / 45 / 70 / 97 / 126 / 150 / 164 / 276 / 285 / 296 天。Gemini 最长 299。Grok 296。→ **多月持有，不是日内或周度轮动。**

## 3. observed：仓位约束——几乎没有

- 单股上限**不存在**：Grok 把 **56.4%** 放在 MU 一只，Gemini 31.8% 在 NEE。
- 持仓数不固定：5 到 16。
- 现金无下限：四家在 0.5–4.2%，基本满仓。只有 Claude 留了 14.6%。
- 股票池**不是窄名单**：出现 EME、CRDO、NBIS、GGG、LDOS、CMS、ZTS、PGR、ISRG、NOC、AZO —— 含中盘股，远宽于大市值名单。

## 4. inference：prompt 的形状

### 高置信（五个家族同时出现，不可能是文风巧合）

| 推断 | 依据 |
|---|---|
| **输出有明确长度上限** | thesis 字符数中位数：Claude 590 / GPT 560 / Gemini 564 / Grok 571 / DeepSeek 570。行数中位数**全部等于 7**。五个家族的自然啰嗦程度差别极大，收敛到这么窄的带宽只能是被约束的 |
| **输出格式是 markdown：`###` 标题 + `-` 项目符号** | 换行数/条：8.49–9.05，五家一致；`###` 开头和 `-` 开头的行在五家都是高频 |
| **要求一条宏观判断** | `- Macro` 作为行首标签在五家都出现（Claude 9、GPT 7、Grok 7、Gemini 3、DeepSeek 多次） |
| **要求逐持仓点评** | 以 ticker 为行首标签的项目符号在五家都高频（`- GOOGL`、`- MU`、`- NOC`、`- ISRG`…） |
| **要求风险/赔率一句** | `Risk/reward`、`Asymmetry` / `Asymmetric setup` 在多家重复出现 |
| **语气被指定为口语化交易员笔记，不是机构备忘录** | 跨家族出现 `### Staying put`、`### Sitting tight` 这类短语，以及"价格走势很丑""市场极度头重脚轻"一类直白表述。财经写作不是任何模型的默认语域 |
| **`hold` 是必须给出的选项，而不是"没动作"** | 五家的 hold 占比 48–74%，且带完整 thesis、`executed=0` |

### 中等置信

- 结构顺序大致是**宏观 → 逐持仓 → 估值/赔率 → 结论**（标签出现位置的次序一致）。
- `### Staying put` / `### Sitting tight` 在五家重复，指向两种可能：prompt 里有 few-shot 示例，或者有一条"先用一句话给出整体立场"的指令。无法区分。

### 拿不到的

逐字 prompt、系统提示、工具清单、证据包构成、他们声称的"每一种可能的金融数据"具体是哪些源。**这些仍然是 `unknown`，而且是这套系统里真正值钱的部分**——不是那几段文字。

## 5. 他们公开数字的三个问题（必须在引用前说清）

1. **起始日不同，收益不可比。** Claude 从 2025-11-25 起（约 319 天），GPT 从 **2026-03-06** 起（约 218 天）。`+77.1%` 和 `+28.4%` 跨的是不同市场窗口。他们自己的排行榜**不是共同起点对照**。
2. **胜率几乎确定只统计已平仓交易。** GPT 的 96.3% 胜率、27 笔交易，同时现金 0.5%、持仓 10 只 —— 浮亏的仓位还开着，不进分母。这是选择性统计产生的数字，不是技能的度量。
3. **Grok 的 +71.5% 是一笔赌注，不是一个策略。** 56.4% 在 MU 一只。这种结果的方差和"策略好坏"几乎无关。

另外 `sharpe_ratio` 字段（Claude 0.211、`volatility` 0.015）看口径像是日频未年化，和 `+28%` 对不上，引用前需要先确认他们的定义。

## 6. 和本项目的差异，以及可执行的结论

| | Rallies | 本项目 |
|---|---|---|
| 股票池 | 宽泛美股，含中盘 | 固定 20 只超大市值 |
| 单股上限 | **无** | 20% |
| 持仓数 / 现金 | 无约束，基本满仓 | ≤10 只，现金是主动选择 |
| 模型输出 | action + symbol + **quantity** + 一段 markdown | **完整目标组合** + 结构化字段 |
| 谁定股数 | 模型 | 模型给权重，代码算股数 |
| 可机检字段 | 无 | `fact_refs` / `evidence_quotes` / `invalidation` / `confidence` |
| 持有期 | 实测 10–299 天 | 模型声明 `horizon_days` |
| 调度 | 每交易日 21:00 UTC 一次 | 目前手动触发 |
| 基准 | SPY（在 `performance_history` 里） | 共用面板里**还没有** |

**可执行的四条：**

1. **我们的 20 只大市值股票池是最大的结构性差异，也可能是最大的自我设限。** 他们贡献收益的名字里有 EME、MU、CRDO、GGG、NBIS —— 我们池子里**一只都没有**。扩池是独立决定（要配套扩数据采集和成本），但它的影响量级比调措辞大得多。
2. **74% 的 hold 率是一个有用的经验先验。** 它说明"每天复核但大部分时候不动"是这类系统的自然状态，不是失败。我们的委托书已经在压换手，这条是支持证据。
3. **他们没有单股上限，所以能产出 +71%（和对应的尾部风险）。** 我们的 20% 上限在结构上就排除了那种结果。这不是缺陷，但意味着**不该把他们的数字当成要追的基准** —— 口径不同。
4. **reasoning 有长度上限，我们没有。** 而 bench 测出我们的模型在当前（很长的）要求下倾向于只建 1–2 个仓位。这两件事可能相关：每个正权重的举证成本太高，于是"少持仓"成了阻力最小的路径。**这是可测的** —— 给 thesis 加长度上限，用 `--repeat 10` 看仓位数和一致性是否变化。比任何措辞猜测都值得先做。

另外应当补上 **SPY 基准序列**：他们在 `performance_history` 里每个快照都存 `spy_price`，这是正确做法，我们的共用面板现在缺这一块。

## 7. 复现方法

```javascript
// 在 https://rallies.ai 任意页面的控制台里
const d = (await (await fetch('/api/get-arena-data-v4?limit=100&feed_model=claude')).json()).arena_data.data;
d.participants[0];        // 账户、绩效、持仓、起始日
d.feed.posts;             // 决策流：post_type / decision_action / symbol / quantity / executed / thesis
d.participants[0].performance_history;   // {snapshot_time, capital, spy_price}
```

`feed_model` 可取 `claude` / `gpt` / `gemini` / `grok` / `deepseek`（`llama` 无参赛者）。`feed.pagination.next_cursor` 可继续翻页。

本文所有统计基于各模型最近 100 条决策与当前持仓快照，抓取时间 2026-10-10。他们的数据会继续变化，结论应按抓取时间理解。
