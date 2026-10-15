# AI Portfolio Arena

把 Claude 和 GPT 当成两个独立的投资经理：给它们**字节一致**的市场证据包，让它们各自管理一个独立的 Alpaca **Paper** 账户，并把每一次决策的论点、引用证据、失效条件和预计持有期完整存档。

**这是 Paper 自动交易实验，不是实盘系统。** 通过确定性校验的目标组合会交给独立执行器，在对应的 Alpaca Paper 账户提交限价委托。绝不连接实盘；Paper 结果不是真实资金表现。两套策略盈利能力**尚未经过验证**，不能据短期结果宣称有效。

灵感来自 [Rallies Arena](https://rallies.ai/arena) 的"实时证据 + AI 投资判断 + 持续复核"思路。**本项目不是 Rallies 的复刻**：它们的提示词、调度频率、工具链和风控约束没有公开到可复制的程度，本仓库里所有股票池、权重上限、调用频率都是我们自己的实验设定（assumption），不是已被证明最优的参数。

---

## 设计主张

这个项目和多数 "LLM 炒股" 项目最大的区别，是把**可审计性**而不是**说服力**当成第一目标。

1. **任何"必须为真"的东西写进 `validate()`，prompt 里只写"希望怎么想"。** prompt 是请求，代码才是约束。模型不能用"我认为这次特殊"绕过任何限额。
2. **不做人格化提示。** 没有"你是巴菲特"。一段"巴菲特式"推理无法被任何机械检查判定为对或错。
3. **模型零工具权限。** `--tools ""` + 冻结数据包，不联网、不读文件。注入面是零；新闻正文里出现"忽略以上规则"也只是数据。
4. **数字不许模型重打。** 模型只能用 `fact_refs`（如 `price:MSFT#mean_20d`）指向数据包字段，由报告层打印数值；写进正文的数字必须在 `evidence_quotes` 里有原文子串，代码逐条校验。
5. **宏观说法必须挂新闻 ID。** 数据包里没有任何宏观序列，所以"10 年期收益率 5.26%"这类说法要么附上报道它的新闻和原文引用，要么不许写。
6. **prompt 本身是实验配置。** 固定指令和用户委托书都落哈希；版本变了就是新实验，净值曲线不许跨版本拼接。
7. **收益不是 prompt 的评价标准。** 改 prompt → 看净值 → 再改，是在 n=1 的路径上做多重检验。`prompt_bench` 只打过程分。

## 系统组成

| 文件 | 作用 |
|---|---|
| `ai_portfolios/config.json` | 实验规则：股票池、单股与总仓上限、模型别名、报价口径 |
| `ai_portfolios/data.py` | GET-only 公共数据采集：Alpaca 日线与报价、新闻摘要、FMP 季度利润表。保留原始响应与 SHA-256 |
| `ai_portfolios/models.py` | 固定研究指令、输出 schema、模型调用、以及全部确定性校验 |
| `ai_portfolios/prompt_bench.py` | 冻结数据包上的离线 prompt 回归台，含提示注入金丝雀 |
| `ai_portfolios/paper_accounts.py` | 只读 Alpaca Paper 账户；Claude/Codex 只读各自凭据，账户与引擎绑定 |
| `ai_portfolios/execution.py` | 唯一券商写模块；只发 Paper DAY 限价单，模型不能访问券商 |
| `ai_portfolios/auto_trader.py` | 每个交易日 10:00 ET 冻结一份数据，再分别运行 Claude 与 Codex |
| `ai_portfolios/peer_review.py` | 两队完成交易轮次后，按同一快照互相评论公开帖子；评论只写入独立档案 |
| `ai_portfolios/console_store.py` | 本机控制台编排：不可改写的轮次档案，完整 prompt / 回复留存 |
| `ai_portfolios/console_server.py` | 本机面板服务（默认 `http://127.0.0.1:8789/`） |
| `ai_portfolios/book.py` / `run.py` / `dashboard.py` | 早期本地模拟记账实验，保留作为历史，不与 Paper 账户结果混算 |
| `ai_portfolios/test_core.py` / `test_console.py` / `engines/test_contract.py` | 核心、控制台及引擎契约回归用例 |

## 独立研究引擎与共同数据

`engines/` 将两支交易团队隔离：`claude_engine` 只调用 Claude、只读 Claude Paper 账户；`codex_engine` 只调用 Codex、只读 Codex Paper 账户。它们共享冻结行情包，但各自拥有 prompt、组合状态、交易历史和账户。旧 2×2 模型评测设计已被本交易目标取代。

每个引擎轮次从 `%USERPROFILE%\\ai-arena-packets`（或 `AI_ARENA_PACKET_STORE`）按字节领取同一个冻结 packet，并记录 packet ID、哈希和冻结时间。引擎 runner 只领取，不会自行采集；没有有效 packet 时先由指定采集方运行 `python -m ai_portfolios.packet_broker freeze`。共享行情凭据位于 `%USERPROFILE%\\ai-arena-secrets\\market-data.env`，Paper 账户凭据位于同目录的 `paper-accounts.env`；凭据审计只输出来源和安全指纹，不输出密钥。

交易周期的默认入口为 `python -m ai_portfolios.auto_trader --once`，服务在 10:00 ET 等待开市后会自动冻结一份共同 packet，分别启动两支引擎。校验失败、账户异常、陈旧报价、已有未完成委托或交易时段关闭时，该账户不会提交新委托。`python -m engines.run --engine claude_engine --no-submit` 或 `--engine codex_engine --no-submit` 仅生成 dry-run；默认运行会尝试向对应 Paper 账户提交限价单。结果网页只读两个团队发布的 JSON 文件。

## 安装

需要 Python 3.10+、以及本机已登录的 **Claude Code CLI** 与 **Codex CLI**（模型调用走这两个已安装工具的登录态，不需要另买 API 订阅）。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Force "$env:USERPROFILE\ai-arena-secrets" | Out-Null
Copy-Item market-data.env.example "$env:USERPROFILE\ai-arena-secrets\market-data.env"
Copy-Item paper-accounts.env.example "$env:USERPROFILE\ai-arena-secrets\paper-accounts.env"
```

凭据放在**仓库之外的共享目录** `%USERPROFILE%\ai-arena-secrets\`（可用 `AI_ARENA_SECRETS` 覆盖），所有团队读同一份，轮换只改一处：

- `market-data.env` — Alpaca 行情 + FMP。仅用于 GET 市场数据端点。应当是一个**独立的第三个** Paper key pair，不是任何一方的账户 key，这样采集方不是参赛方之一。
- `paper-accounts.env` — 两个独立 Paper 账户，每个模型一个。每个团队只应读自己那一个：`load_credentials(only="gpt")`。

解析顺序是进程环境变量 → 共享目录 → 工作区本地 `.env` / `.env.aitrading`（保留以便旧 checkout 仍能跑）。不要提交，也不要粘进任何报告或聊天。

想确认两边用的是同一个行情来源而又不暴露 key，看 `credentials.audit()`：它只输出来源和指纹，secret 那一半连指纹都不给。

## 运行

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Start-SharedOutput.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\Install-AutoTraderTask.ps1
```

浏览器打开 `http://127.0.0.1:8790/` 查看共享只读结果。第二条命令安装每日交易任务：Windows 在本机每天 7:00 启动，程序等待到 10:00 ET 后检查市场时段，再运行两支团队。

两支团队都完成后，协调器会再调用一次 Claude 和一次 Codex，对方此轮公开的决策帖子各获得一条事后评论。互评失败不会改变交易状态；评论不会进入下一轮输入，也不触发订单。每天新增的两次调用可能使用对应 CLI 的用量额度，结果保存在 `reports/peer_reviews/`。互评只包含公开的决策摘要、目标持仓论点和被引用证据标题，不发送账户快照、订单、完整 prompt 或研究轨迹。详见 [`docs/peer_review_design_20261015.md`](docs/peer_review_design_20261015.md)。

不要把本机面板公开到互联网。旧研究控制台仍可在 8789 使用；8790 的共享面板只读 JSON 发布文件，不写控制台数据。

### prompt 回归（改 prompt 之前先跑这个）

```powershell
.\.venv\Scripts\python.exe -m ai_portfolios.prompt_bench --packet reports/ai_paper_console/rounds/<id>/packet.json --holdings reports/ai_paper_console/rounds/<id>/accounts_before.json --provider claude --repeat 10 --label my-candidate
```

在**已保存**的数据包上重复调用同一个模型，输出 schema 通过率、引文忠实率、未挂钩宏观说法数、可机检失效条件比例、重复之间的持仓 Jaccard 一致性和成本。`--inject` 换成提示注入金丝雀模式；`--score-only <目录>` 对已保存回复免费重算指标；`--dry-run` 只打印 prompt 哈希不调模型。

单次成功毫无信息量：同一条措辞在两轮里可以一轮遵守一轮不遵守。所有 prompt 变更都用重复 N 次的分布判断。

## 范围边界

- 只研究**做多美股**，不加杠杆、不做期权、不做空。
- 行情源当前是 IEX 单一场所：不是全市场成交量，也不是完整 NBBO。
- 日线只用已完成的前一交易日及更早，不把当日未完成日线当完整日线。
- 财务数据是单一供应商的当前 vintage，且只有最近两个季度 —— 两个季度不构成趋势。
- FMP 免费档对部分股票返回 HTTP 402，这会被原样记录为数据缺口，不是公司基本面信号，也不允许用估算值填补。
- 预计持有天数是模型意见，不是强制到期卖出；减仓和退出由后续目标组合生成限价委托。
- 结构校验通过**不代表投资判断正确**。

## 审计与分类标注

每一轮都保存：原始 API 响应、冻结的数据包、实际发送的完整 prompt、未经编辑的模型回复、校验结果或失败原因、用量与成本、以及源码哈希。

所有数字按来源标注：

| 分类 | 含义 |
|---|---|
| `observed` | 接口原样返回的内容（含 Paper 账户记录，不是真实资金成交） |
| `derived` | 由行情计算出的特征 |
| `assumption` | AI 的投资判断、实验限额、压力成本假设 |
| `unknown` | 未取得的数据、真实费用与滑点、未来收益 |

本项目曾位于一个含实盘系统的仓库内，当时每轮会记录外部真相账本的快照号。独立后 `external_truth_ledger` 默认为 `null`，此时如实记为 `unknown`，不用猜测值冒充审计锚点。

## 文档

- [`docs/claude_gpt_portfolio_prompt_design_20261009.md`](docs/claude_gpt_portfolio_prompt_design_20261009.md) — prompt 分层设计、已观测的失败模式、以及为什么不能用净值评价 prompt
- [`docs/claude_gpt_portfolio_team_implementation_guide_20261009.md`](docs/claude_gpt_portfolio_team_implementation_guide_20261009.md) — 团队实施与交接指南，含硬性验收清单和已知缺口
- [`ai_portfolios/PAPER_CONSOLE_README.md`](ai_portfolios/PAPER_CONSOLE_README.md) — 控制台操作说明与常见问题

## 已知缺口

见交接指南第 8 节。摘要：本机与报价时钟存在亚秒差异；单模型失败时的退出码语义需要修复；两模型与基准的共同起点尚未对齐，因此**目前还不能做模型排名**；企业行动（股息、拆股、并购、退市）处理不完整，因此**不能宣称可投资业绩**；无人值守调度尚未部署，也不应在恢复语义验证完成前部署。

## 许可与免责

本仓库不构成投资建议，不授权也不启动真实资金交易。作者不是持牌投资顾问。使用者自行承担一切后果。
