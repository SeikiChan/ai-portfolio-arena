# 共同证据包存储

两个独立团队只有在看到**完全相同的证据**时才可比。各自采集会让"答案不同"同时可能来自判断不同、价格不同或新闻不同 —— 到那时模型比较和引擎比较一起作废，而且事后无法补救。

所以：**一轮只冻结一份 packet，所有团队读同一份字节。**

本文件是规格。实现方只需要本文件，**不需要读任何一方的代码**。

---

## 一、位置：在所有团队工作区之外

```
%USERPROFILE%\ai-arena-packets\          ← 默认；用环境变量 AI_ARENA_PACKET_STORE 覆盖
├─ current.json                          ← 当前有效包的指针
├─ freeze.lock                           ← 单一采集锁
└─ rounds\<packet_id>\
   ├─ packet.json                        ← 冻结的证据，字节不可变
   ├─ store_manifest.json                ← 身份、哈希、采集方、代码哈希
   ├─ manifest.json                      ← 采集清单：来源、行数、各原始响应哈希
   └─ raw\*.json                         ← 原始 API 响应
```

**这不违反"不读取另一个团队的 workspace"。** 冻结的 packet 是两个团队的**共享输入**，不是对方的 state；而且读一个目录不等于 import 对方的代码。把输入也隔离掉，整个对照实验才真的作废。

`packet_id` 格式 `%Y%m%dT%H%M%S%fZ`（UTC）。

## 二、`current.json`

```json
{
  "packet_id": "20261010T120000123456Z",
  "frozen_at": "2026-10-10T12:00:00.123456+00:00",
  "packet_sha256": "…",
  "collector": "paper_console",
  "lifetime_hours": 24,
  "created_at": "…",
  "source_code": {"data.py": "…"},
  "classification": {"market_data": "observed", "price_features": "derived"}
}
```

`frozen_at` 来自 packet 自身，不是写指针的时间。**有效期由读取方自己按当前时间算**，不要信文件里可能写着的"expires"字段 —— 过期判断必须是 `now - frozen_at > lifetime_hours`。

## 三、消费方要做的全部事情

读指针 → 校验哈希 → **按字节复制**进本轮目录 → 在 `round.json` 里记下身份。十几行，不依赖任何一方的代码：

```python
import hashlib, json, shutil
from pathlib import Path
from datetime import datetime, timezone

STORE = Path.home() / "ai-arena-packets"

def claim(round_dir):
    pointer = json.loads((STORE / "current.json").read_text(encoding="utf-8"))
    frozen = datetime.fromisoformat(pointer["frozen_at"])
    if (datetime.now(timezone.utc) - frozen).total_seconds() > pointer["lifetime_hours"] * 3600:
        raise SystemExit("当前 packet 已过期：要么由采集方冻结新的一轮，要么明确加入旧轮次")
    source = STORE / "rounds" / pointer["packet_id"] / "packet.json"
    if hashlib.sha256(source.read_bytes()).hexdigest() != pointer["packet_sha256"]:
        raise SystemExit("packet 与记录哈希不符，拒绝使用")
    Path(round_dir).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, Path(round_dir) / "packet.json")   # 复制字节，不要重新序列化
    return pointer
```

**必须按字节复制。** 用 `json.load` 再 `json.dump` 会改变字节，哈希就对不上了，契约校验会拒绝这一轮。

然后在 `round.json` 里记：

```json
{"packet_id": "…", "packet_sha256": "…", "packet_frozen_at": "…"}
```

两轮要被当成一对来比较，这两个字段必须完全相等。只有 `packet_sha256` 相等而 `packet_id` 不同、或反之，都视为存储被改动过，拒绝配对。

## 四、消费方不要做的事

- **不要调用自己的 `collect()`**，不要发起任何市场数据请求。各自采集就是这份文件要防的事。
- **不要往 store 里写。** 只有采集方写，而且只用独占创建（`open(..., "x")`）。冻结过的包永不覆盖、永不编辑、永不用同一个 id 重新生成。
- **不要因为包"有点旧"就自己重新采一份。** 旧包说明这一轮的共同起点就是那个时刻；两边都用它才是对的。真要新数据，就由采集方冻结新一轮，两边一起用新的。

## 五、采集方

本仓库提供 CLI，可以当独立进程调用（不需要 import）：

```bash
python -m ai_portfolios.packet_broker current                 # 看当前包，过期则退出码 1
python -m ai_portfolios.packet_broker freeze                  # 加锁、采集一份、更新指针
python -m ai_portfolios.packet_broker verify                   # 重算哈希比对
python -m ai_portfolios.packet_broker claim --into <轮次目录>  # 领取并复制进本轮
```

`freeze` 持有 `freeze.lock`，所以两个进程同时冻结不会产生两份。谁冻结都行 —— Paper 面板跑一轮时会自动领取，没有有效包就顺手冻一份。

## 六、行情凭据也是共享的

采集用的行情凭据从 `%USERPROFILE%\ai-arena-secrets\market-data.env` 读取，所有团队同一份。
解析顺序：进程环境变量 → 共享目录 → 工作区本地文件（保留以便旧 checkout 仍能跑）。

这一对 key 是**独立的第三个 Paper key pair**，既不是 Claude 账户也不是 GPT 账户，所以采集方不是参赛方之一。
（旧代码会把它覆盖成 GPT 账户的 key，那个不对称没有任何理由，已经去掉。哪个 paper key 去请求不会改变返回的数据，
但这件事得读代码才能发现，所以不该留着。）

账户 key 在同目录的 `paper-accounts.env`。**每个团队只应读自己那一个**：

```python
load_credentials(only="gpt")      # 只解析 GPT 账户，另一个根本不读
```

两边可以在不暴露 key 的前提下验证用的是同一个来源：`store_manifest.json` 里有
`market_data_key_fingerprint`（sha256 前 10 位），`python -m ai_portfolios.credentials` 式的
`credentials.audit()` 会输出每个凭据的来源和指纹，**从不输出值**，secret 那一半连指纹都不给。

两个文件都不要提交、不要粘进报告或聊天。轮换只需改共享目录这一处。

## 七、分类标注

store 布局、字段与有效期（24 小时）是 `assumption`（工程约定，沿用既有的计划有效期先例）。packet 内容的分类沿用采集器自身的标注：行情为 `observed`，派生特征为 `derived`。"两边看到同一份字节"是可由哈希验证的 `observed` 事实，不是声明。
