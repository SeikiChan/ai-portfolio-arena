# 模型自主研究工具

用户已明确授权增加研究权限。本版本提供同一 provider 的自主研究阶段，配置只为 `codex_engine` 启用，保留 Claude 团队独立完成的实现；不修改股票池、仓位、券商写入路径或订单权限。

## 能做什么

| 工具 | 数据来源 / 行为 | 限制 |
| --- | --- | --- |
| `search_web` | Bing RSS 公开网页搜索 | 最多六项结果；搜索摘要不是全文 |
| `search_news` | Google News RSS 新闻搜索 | 最多六项结果；聚合链接可能无法直接取得原文 |
| `search_papers` | arXiv API，支持 `all:portfolio` 等查询 | 最多六篇；可能被限流 |
| `search_scholarly` | Crossref 文献检索、DOI 与可用全文链接 | 元数据不是全文，也不能证明同行评审质量 |
| `read_url` | 公开 HTTPS 网页、披露、JSON、PDF | 无登录、无付费墙绕过、无 JavaScript 浏览器；PDF 每次五页，可指定下一页；无 OCR |
| `calculate` | 数字、括号、加减乘除 | 不执行模型代码；操作数来源仍须核实 |

模型自己选择问题、查询、来源和后续阅读。不是预先替模型选定论文。公开公司投资者关系页面、公开研究报告和监管披露可通过搜索与 URL 读取访问；站点拒绝时记为 unknown。

这是宿主实现的工具调用循环：模型返回结构化动作，宿主执行工具，结果再提供给同一模型。不是只在 prompt 里声称有工具。CLI 本身的任意命令和原生工具仍被关闭。

## 边界与资源

- 研究输入只有公共数据包及股票池，不含账户快照、余额、密钥、其他团队记录。
- 工具只发无凭据 HTTPS GET。拒绝内网、回环、组播、用户密码 URL、非标准端口；每次重定向重新检查，连接固定到已验证的 DNS 地址。
- 下载最多 4 MiB，每次工具最多 40 秒，工具进程最多 512 MiB 内存。PDF 解压也在该进程内。
- 每轮最多六次规划、十二次工具调用、十分钟研究。预算是防止死循环的实验设置，不是交易 KPI；修改预算会生成新实验身份。
- 最终组合决策仍只读冻结输入，继续使用原有 schema 和校验。研究来源是 `kind=research`，不会冒充审计财务或自动满足原有价格加新闻/财务的最低证据要求。宏观字段仍遵守已有新闻引用要求。
- 模型或规划格式失败会使该轮失败，不进入执行。单个网站失败会回传 unknown，模型可以选择其他来源；不偷偷用旧价格或捏造资料补齐。

## 档案与时间

原 `packet.json` 字节不变。新增文件位于每队本轮目录，全部由原轮次 receipt 哈希封存：

- `research_XX_<provider>_prompt.json`、`response.json`、`process.json`：规划输入、模型输出、CLI 运行结果。
- `research_XX_request.json`、`result.json`、`source.bin`：工具请求、提取结果、原始响应（如成功取得）。
- `research_manifest.json`：round_id、snapshot_id、观察区间、工具次数、证据行数、文件哈希和脚本。
- `decision_packet.json`：实际交给最终决策的扩展证据包；包含基础行情冻结时刻和研究完成时刻。

网页取得时间不是发布时间。搜索结果、论文和公司陈述均是来源实际返回内容（observed），不自动成为已确认经济事实。算术结果属于 derived，但未经来源验证的模型输入仍是 assumption。缺失来源是 unknown。

两队可能选择不同来源并在不同时刻结束研究，因此不能将结果称为“同证据严格模型对照”。不要在历史数据包上重新联网后声称完成无前视回测。

## 实验与运行

`ai_portfolios/config.json` 的 `research.enabled=true` 与 `research.engines` 共同选择启用的团队；当前仅含 `codex_engine`，该团队下一次新轮次调用研究阶段。直接调用 engine.decide 或离线 prompt bench 不会联网。

新增 experiment_id 包含工具实现、策略预算、模型配置、引擎版本、固定指令和用户委托。开启和关闭研究均会产生不同实验 ID，历史决策只从同 ID 加载；真实 Paper 旧持仓不会凭空清空。导出每轮带 experiment_id，禁止跨 ID 拼接收益。

默认运行命令仍会尝试 Paper 执行。验证研究时应使用 `--no-submit`；这仍读取自己的 Paper 账户但不提交订单。该开发变更本身不启动交易进程或计划任务。

## 验证与评价

离线测试：`python -m pytest engines ai_portfolios -q`。包括独立编写的 SSRF、格式、算术、PDF、凭据、预算和证据完整性对抗测试，以及完整轮次失败阻断和封存测试。

来源实现参考：[arXiv API](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/user-manual.md)、[Crossref API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[pypdf](https://pypdf.readthedocs.io/en/stable/modules/PdfReader.html)。

功能检查不能证明收益更高，也不是 prompt 质量评估。评价投资输出时，必须固定保存的扩展数据包和账户输入，每个候选 `--repeat 10`，按仓库规则检查结构、引文、无依据宏观说法和一致性；不得以短期净值筛选 prompt。

尚不提供自动修改自身指令、长期反思记忆、任意 Python 回测、账户工具、网页登录、付费订阅或实盘权限。本变更只实现公开研究能力。
