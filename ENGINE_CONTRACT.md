# 研究引擎契约

本仓库允许存在**多个研究引擎**，每个引擎由不同的作者（人或 AI 开发工具）独立设计，各占一个文件夹。
引擎之间互不调用、互不读取对方的 prompt 或输出。

共享的只有两样东西：**上游的冻结证据包**，和**下游的发布边界**。中间全部独立。

```
        ┌──────────────────────────────┐
        │  一轮冻结一份 packet.json     │   共享输入：两个引擎读同一个文件
        │  （谁采集都行，但只采一次）    │   引擎自己不允许采集
        └──────────────┬───────────────┘
            ┌──────────┴──────────┐
            ▼                     ▼
┌───────────────────┐   ┌───────────────────┐
│  claude_engine    │   │  codex_engine     │   各自独立：指令、schema、
│  指令/schema/校验  │   │  指令/schema/校验  │   校验、后处理、回归台
│  跑 Claude + GPT  │   │  跑 Claude + GPT  │   互不导入、互不读取
└─────────┬─────────┘   └─────────┬─────────┘
          ▼                       ▼
   exports/dashboard.json   exports/dashboard.json   发布边界：只读 JSON
          └───────────┬───────────┘
                      ▼
            ┌──────────────────┐
            │  共用可视化面板   │   只读已发布结果，不导入任何引擎代码
            └──────────────────┘
```

**发布边界沿用 Codex 已经实现的那套**（`team_exports.py`：版本化的 `exports/dashboard.json`，注释写明
"Never import code or state from another team's workspace"）。那个设计比让面板直接 import 引擎更好：
两边零代码耦合，一个引擎坏掉不会让面板或另一个引擎停摆，而且跨目录、跨仓库都成立。本仓库的
`engines/publish.py` 服从那个 schema，不另起一套。

---

## 一、最重要的一条：每个引擎都必须同时跑两个模型

直觉上的分法是"Claude 写的引擎跑 Claude，Codex 写的引擎跑 GPT"。**这个分法会永久毁掉这个实验**，因为它把"作者"和"模型"焊在一起：到最后你无法分辨赢的是 Claude 这个**模型**，还是 Claude 这个**prompt 作者**。

正确的做法是一个 2×2 因子设计。**每个引擎都必须在同一份冻结证据包上跑 Claude 和 GPT 两个模型。**

|              | claude_engine | codex_engine |
| ------------ | ------------- | ------------ |
| Claude 模型  | ✓             | ✓            |
| GPT 模型     | ✓             | ✓            |

于是：

- **行间差异** = 模型效应（原本的那个问题："Claude 和 GPT 谁更强"）
- **列间差异** = 引擎 / 作者效应
- **交互项** = 作者偏置：某个引擎是否对与它同源的模型更友好

只填对角线两格（Claude 引擎跑 Claude、Codex 引擎跑 GPT），这三项永远分不开。

成本上这是便宜的：一轮四次调用，其中后三次命中前缀缓存，实测约 $0.17 + 3 × $0.03。

## 二、证据包必须共享，不能各自采集

一轮研究只冻结**一份** `packet.json`，所有引擎读同一个文件。

引擎**不允许**调用 `collect()`，也不允许自己发起任何市场数据请求。各自采集会让两个引擎看到不同的价格和新闻，那时候连模型比较都不成立，更不用说引擎比较。

这一条是对"互不读取对方 workspace"的**明确例外**，必须单独写出来：冻结的证据包不是另一个团队的
state，而是两个团队的**共享输入**。把它也隔离掉，整个对照实验就作废了。

落地方式：packet 写在一个两边都只读的中立位置，`round.json` 里记 `packet_sha256`，
`engines/contract.py` 校验目录里的 packet 与声明的哈希一致。两个引擎同一轮的 `packet_sha256`
不相等，这一轮就不是对照轮。

## 三、引擎接口

每个引擎目录必须导出一个 `ENGINE` 对象，满足 `engines/contract.py` 里的检查：

| 属性 / 方法 | 含义 |
| --- | --- |
| `engine_id` | 目录名，同时是结果目录名。只含小写字母、数字和下划线 |
| `author` | 这份引擎设计的责任方（`"Claude"` / `"Codex"` / 人名） |
| `version` | 引擎语义版本。指令、schema 或校验语义变了就要变 |
| `instruction(portfolio_mode)` | 返回该引擎的固定研究指令全文 |
| `schema()` | 返回该引擎要求模型输出的 JSON schema |
| `decide(provider, packet, previous, mandate, directory, config)` | 对单个模型跑一轮，返回 status 字典，并把 prompt / 原始回复 / 状态写进 `directory`。`config` 是共享实验配置，引擎不得修改它 |

`decide` 的 `provider` 取 `"claude"` 或 `"gpt"`，**两个都必须支持**。引擎不得根据 provider 改变指令内容或校验严格程度；provider 只能影响 CLI 调用方式（命令行参数、schema 传递方式、reasoning effort），且必须记录在 status 里。

## 四、结果契约：一轮一个目录

引擎把结果写进 `reports/engines/<engine_id>/rounds/<round_id>/`，文件布局与现有面板一致，面板据此直接渲染，无需为新引擎改前端。

必须存在：

| 文件 | 要求 |
| --- | --- |
| `round.json` | 见下方必填字段 |
| `packet.json` | 本轮共享证据包的副本或硬链接；其 sha256 必须与 `round.json` 里的 `packet_sha256` 一致 |
| `<provider>_prompt.txt` | 实际发给 CLI 的完整正文 |
| `<provider>_status.json` | 结果、用量、校验结论或失败原因 |
| `receipt.json` | 完成回执，含各文件哈希与源码哈希 |

`round.json` 必填字段：

```json
{
  "id": "<round_id>",
  "engine_id": "claude_engine",
  "engine_version": "1.0.0",
  "engine_author": "Claude",
  "providers": ["claude", "gpt"],
  "environment": "alpaca_paper",
  "packet_sha256": "...",
  "instruction_sha256": "...",
  "mandate_sha256": {"claude": "...", "gpt": "..."},
  "strict_comparison": true,
  "reasoning_effort": "medium",
  "orders_submitted": false,
  "status": "completed | partial_failure | failed",
  "started_at": "...",
  "completed_at": "..."
}
```

`strict_comparison` 只有在两个 provider 的 mandate 哈希一致、且两者都在同一个 packet 上运行时才为 `true`。为 `false` 的轮次**不得进入任何模型排名**。

`orders_submitted` 必须存在且为 `false`。

## 五、所有引擎都不许改的不变量

改动以下任何一项，必须新建实验 ID，并且不允许把新旧轮次的收益曲线接起来：

1. **不存在下单路径。** 任何引入提交、撤销、修改或平仓订单的代码一律拒绝。券商域名硬锁定在 `https://paper-api.alpaca.markets`。
2. **结果目录只追加，不覆盖。** 轮次档案不可改写；失败就如实留失败。
3. **证据包共享且冻结。** 见第二条。
4. **模型零工具权限。** 不联网、不读文件、不执行命令。
5. **分类标注纪律。** `observed` / `derived` / `assumption` / `unknown`，缺数据不许用猜测或 fallback 填补。
6. **失败不得被报告为成功。** 单模型失败时，成功那一侧的结果必须保留，不得重跑抽签择优。
7. **重试只针对失败，且必须记录。** 不允许对已成功的输出反复调用择优。

## 六、引擎自己可以决定的事

指令措辞与结构、输出 schema 的字段、`validate()` 的检查项与严格程度、是否把权重交给代码计算、复盘与校准反馈的设计、回归台的指标。

**这一层就是实验本身。** 引擎之间在这里分歧越大越好 —— 那才是可测的东西。

## 七、裁决与报告

谁的结果更好，由共享的机械裁决台输出，不由任何引擎的作者撰写。裁决台遍历所有引擎的轮次档案，按各引擎自己声明的失效条件机械判定是否触发，并按 2×2 分组汇总。

裁决台应当由**不写该引擎**的一方实现，另一方写对抗测试。原因是结构性的：引擎作者同时是被评测模型的同源方，让裁判和被评测方分离是免费的。

## 八、分类标注

本契约本身是 `assumption`（工程实验设定），不是已被证明最优的实验设计。第一条的 2×2 必要性是 `derived`（来自混淆项的定义），各引擎的投资判断是 `assumption`，真实收益与作者偏置的实际大小目前是 `unknown`。
