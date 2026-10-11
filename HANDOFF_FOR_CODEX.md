# 给 Codex 团队的交接

写给刚接手、对这个项目不熟的人。**读这一份就够开工**，不需要读另一边的代码。

---

## 这是什么

两个 LLM 各自管理一个独立的 Alpaca **Paper** 账户。给它们**字节一致**的市场证据包，各自输出目标组合，全程存档：实际发送的 prompt、未经编辑的回复、校验结果、源码哈希。

目的是回答"Claude 和 GPT 谁的投资判断更好"。**盈利能力尚未验证**，现在还没有任何可比的业绩。

你们负责一个**独立的研究引擎**：自己的指令、schema、校验、回归台，工作区是 `alpaca-gpt-research/gpt_research/`。我们不互相 import，也不读对方的 prompt。

---

## 第一天：三条必须先知道的事实

**1. 正式仓库变了。** 现在是 `ai-portfolio-arena`（私有 GitHub，本地 `C:\Users\Allen\ai-portfolio-arena`）。`alpaca-live\ai_portfolios\` 是**旧副本**，不要再在那里改东西。

你们之前在 alpaca-live 上做的面板工作（`team_exports.py`、`/api/teams/gpt`、"独立团队研究"页签、拒绝在共享面板跑 GPT 的保护）**已经原样搬过来了**，PR #2，一个字符都没改，然后才在上面做 additive 泛化。那个只读发布边界比我原本打算的（面板直接 import 引擎）更好，所以我改成服从你们那套 schema，不另起一套。

**2. 两边都还是 0 轮。** 你们的 `exports/dashboard.json` 是 `initialized_no_runs`，我们的引擎目录是空的。所以下面要改的东西**现在改是零成本的**；跑过几轮再改就要作废重来。

**3. codex CLI 的额度现在是用完的状态。** 实测返回 `You've hit your usage limit`。这不是代码故障 —— PR #4 把它分类成 `usage_limit_reached` 并给出提示。另外 codex 二进制不在 PATH 上，它在 `~/.codex/plugins/.plugin-appserver/codex.exe`，PR #4 加了兜底解析。

---

## 你们要改的四处，按"做晚了要作废"排序

### 1）停止自己采集行情 ← 最紧急

`gpt_research/engine/data.py` 里有自己的 `collect()`，直接请求 Alpaca 和 FMP。

**两边各自冻结 packet，模型比较和引擎比较会同时作废** —— 到那时"答案不同"同时可能来自判断不同、价格不同、新闻不同，事后无法分离。

改法在 `PACKET_STORE.md` 第三节，**十几行，不依赖我们任何代码**：读 `%USERPROFILE%\ai-arena-packets\current.json` → 校验 sha256 → **按字节复制** `packet.json` 进本轮目录。

```python
shutil.copyfile(source, Path(round_dir) / "packet.json")   # 复制字节
```

**必须按字节复制。** `json.load` 再 `json.dump` 会改变字节，哈希对不上，契约校验会拒绝这一轮。

采集能力可以留着（谁冻结都行，`python -m ai_portfolios.packet_broker freeze` 也行），但**轮次流程里不能采集**。

### 2）`round.json` 加身份字段

```json
{
  "packet_id": "...", "packet_sha256": "...", "packet_frozen_at": "...",
  "engine_id": "codex_engine", "engine_version": "...", "engine_author": "Codex"
}
```

前三个让两边的轮次能被认定为"同一份证据的一对"；后三个是引擎契约的必填项（`ENGINE_CONTRACT.md` 第四节），缺了就进不了共用面板，也进不了对照表。

我们这边的轮次目前也缺后三个，同样要补 —— 这不是只要求你们。

### 3）凭据改从共享目录读

`%USERPROFILE%\ai-arena-secrets\`：

- `market-data.env` — 行情 + FMP。**你们不用自己配了**，共享目录里已经有。而且这一对经指纹比对确认是**独立的第三个 Paper key**，既不是 Claude 账户也不是 GPT 账户，所以采集方不是参赛方之一。
- `paper-accounts.env` — 两个账户。**只读你们自己那一个**：`load_credentials(only="gpt")`。

解析顺序：进程环境变量 → 共享目录 → 工作区本地文件（保留以便旧 checkout 仍能跑）。

要在不暴露 key 的前提下确认两边用的是同一个行情来源：对比 `store_manifest.json` 里的 `market_data_key_fingerprint`（sha256 前 10 位）。**不要贴 key 值**，贴指纹。

### 4）每个引擎都必须跑**两个**模型

直觉分法是"你们的引擎跑 GPT，我们的跑 Claude"。**这个分法会永久毁掉实验**，因为它把"作者"和"模型"焊在一起 —— 最后无法分辨赢的是 GPT 这个**模型**，还是 GPT 这个**prompt 作者**。

要四格全填：

| | codex_engine | claude_engine |
|---|---|---|
| **GPT 模型** | ✓ | ✓ |
| **Claude 模型** | ✓ | ✓ |

行间差 = 模型效应｜列间差 = 引擎/作者效应｜**交互项 = 作者偏置**

`engines/contract.py` 的 `cell_coverage()` 在四格填满前一律返回 `separable: false`，发布时会打印警告。

**这不需要动你们在 `console_server.py` 里加的那条"拒绝在共享面板跑 GPT"的保护**，我原样保留了。那条拦的是"共享面板不碰另一个团队的 workspace"，完全正确。而引擎用自己的 prompt 去调另一个模型的 CLI，没有触碰对方任何代码或状态 —— 走 research-only 的回归台（合成空账本、不碰账户）就能填满四格。顺带这也省掉"要不要四个 Alpaca 账户"的麻烦。

（提醒：那条保护只在 `console_server.py`（HTTP 层），`console_store.py`（库层）不拦。我验证时就这么误启了一轮 GPT。要不要两层都拦，由你们定。）

---

## 三件别做的事

**1. 别照着我们的指令改。** `claude_engine` 的固定指令是 Claude 写的，而被评测的模型之一就是 Claude —— 这份 prompt 可能是"Claude 形状"的。你们那版**必须从规格独立写**（`ENGINE_CONTRACT.md` + `docs/claude_gpt_portfolio_prompt_design_20261009.md`），不要从我们那份文本出发。两版同源，作者偏置就测不出来了，这个槽位的全部价值就没了。

**2. 别用 paper 净值曲线评价 prompt 改动。** 那是在 n=1 的路径上做多重检验。用冻结 packet 上的离线回归台，`--repeat 10`，看 schema 通过率、引文忠实率、未挂钩宏观说法、重复之间的持仓一致性。**单次成功毫无信息量** —— 实测过同一份指令 Claude 一轮遵守一轮不遵守。

**3. 别把失败报告成成功。** 单模型失败时，成功那一侧的结果必须保留，不得重跑抽签择优。额度用完、登录过期、真故障要分开（`classify_failure` 分三种）。

---

## 验证命令

```bash
python -m ai_portfolios.packet_broker current      # 看当前共享 packet，过期退出码 1
python -m ai_portfolios.packet_broker freeze       # 加锁冻结一份（谁都能冻）
python -m ai_portfolios.packet_broker verify       # 重算哈希比对
python -m ai_portfolios.execution --provider gpt   # dry run：打印会发什么委托，不碰券商
python -m pytest ai_portfolios/ engines/ -q        # 全套
```

执行层已经做好了（PR #5），**默认 dry run**，`POST /v2/orders` 是唯一写接口，没有撤单/改单/平仓。`client_order_id` = (轮次, 模型, 标的, 方向)，所以同一份计划重复跑也只下一次单。

---

## 分工：裁决台建议由你们写

"谁的结果更好"的机械裁决（判定模型声称的失效条件有没有在期限内触发、按 2×2 分组汇总），**建议由 Codex 实现，我们写对抗测试**。

理由是结构性的：这是全系统唯一"作者即裁判"的地方，而我们是 Claude 的同源方。让裁判和被评测方分离是免费的。反过来，我们写的指令和 `validate()`，欢迎你们写专门想打穿它的测试。

**每一层由另一方写对抗测试** —— 这是让作者身份变得无关的唯一办法，而且写测试比写实现快。

分歧用测试裁决，不用资历裁决：写不出能区分两种行为的测试，说明这个分歧对系统没有可观测影响，随便选一个继续干。

---

## 当前 PR（都在 `ai-portfolio-arena`）

| PR | 内容 |
|---|---|
| #1 | 引擎边界、共同房规 `AGENTS.md`、发布导出 |
| #2 | **你们的面板工作搬运** + 多团队支持 |
| #3 | 共享 packet + 共享凭据 |
| #4 | 两个 CLI 的 PATH 兜底 + 失败分类 |
| #5 | Paper 下单执行（基于 #3） |
| #6 | 从公开数据反推 Rallies 的运行规则 |

#1–#4 文件零重叠，做过真实试合并，172 passed。**`main` 目前缺凭据解析（在 #3 里）**，所以先合 #3 再开工最省事。

#2 里我改了你们四个文件（`team_exports.py` / `console_server.py` / `app.js` / `index.html`），改动都是 additive、`read_gpt_export` 行为完全没变。理由是"共用网站"的要求下只显示一个团队等于只交付一半。如果你们更想自己做那层泛化，revert 第二步即可。

---

## 一条可能有用的外部观察

PR #6 从 Rallies Arena 的公开端点拉了五个模型的全部决策做统计。两条和引擎设计相关：

- **他们的 reasoning 有长度上限**（五个模型家族的 thesis 中位数全在 560–590 字符、行数中位数全部等于 7），而我们两边都没有。我们的回归台测出模型在当前较重的举证要求下只建 1–2 个仓位，他们一段 7 行的笔记就拿 5–16 只。**这两件事可能相关，而且可测。**
- **他们没有单股上限**（Grok 56% 在一只 MU 上），股票池也宽得多（含中盘）。所以不要把他们的收益数字当基准 —— 口径完全不同，而且他们的排行榜起始日不一致。

详见 `docs/rallies_behavioral_inference_20261010.md`。

---

## 文档索引

| 文件 | 看它干什么 |
|---|---|
| `ENGINE_CONTRACT.md` | 引擎接口、轮次目录契约、2×2 要求、不可改的不变量 |
| `PACKET_STORE.md` | 共享 packet 规格，第三节是可直接抄的客户端 |
| `AGENTS.md` | 所有 agent 共用的房规、数据分类纪律、协作机械规则（在 PR #1） |
| `docs/claude_gpt_portfolio_prompt_design_20261009.md` | prompt 分层设计、已观测的失败模式、为什么不能用净值评价 prompt |
| `docs/claude_gpt_portfolio_team_implementation_guide_20261009.md` | 原始交接指南，含硬性验收清单和已知缺口（较长） |
| `ai_portfolios/PAPER_CONSOLE_README.md` | 控制台操作与常见问题 |

有判断上的分歧，在对应 PR 下写评论就行 —— 这些边界文件本来就是给两边共同约束的，不是单方面定的。
