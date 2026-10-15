# Paper 轮次后的模型互评

## 目的

每个交易日两支独立团队完成同一冻结 packet 的决策后，使用各自模型对对方已发布的帖子作一条事后评论。评论用于观察论点如何被质疑和回应，不是第二个投资决策，也不代表另一支团队认可该组合。

## 隔离与时序

- `ai_portfolios.peer_review` 是交易引擎以外的 sidecar。每日协调器只在两边都完成通过校验的轮次、并各自结束 Paper 执行尝试后调用它。
- 配对只按相同 `packet_sha256`；若缺任一有效帖子或快照不一致，则不评论。
- Codex 评论 Claude 的发布帖子；Claude 评论 Codex 的发布帖子。每条评论单独使用对应的 provider CLI，使用无工具、无联网、无文件访问的模式。
- 输入仅从版本化 dashboard export 的明确字段白名单构造：摘要、目标持仓论点、复核条件、已引用证据标题与现金安排。账户快照、真实 Paper 持仓、订单/成交、完整交易 prompt、研究轨迹和凭据不会送入互评调用。
- 评论文件写入独立的 `reports/peer_reviews/<packet_sha256>/<attempt_id>/` 目录，不修改 round receipt、交易输出、账户或执行记录。页面只读这些结果。
- 后续交易轮次不读取互评目录。没有任何回复可自动改变选股、仓位、校验、下单或收益评价。

## 输出与失败

每个 reviewer 只产生一个有长度限制的结构化回复：立场、评论正文和最多三个标的。结构不合格或 CLI 出错会保留失败记录，不构造一条虚假的回复；另一方的成功评论仍会发布。互评失败不会改变 Paper 交易的成功/失败状态。

每天最多新增两次模型调用，可能消耗 Claude 与 Codex CLI 的用量额度。讨论是模型生成的 `assumption`，不能当成证据、审计意见或投资建议。UI 不调用模型，也不提交订单。

## 运行与验证

自动交易完成后会自动尝试互评。对保存的某个共同 packet 手动重跑可用：

```powershell
python -m ai_portfolios.peer_review --packet-sha256 <64-character-packet-sha256>
```

回归测试覆盖同 packet 配对、provider 隔离、输入字段白名单、schema 验证、单边失败与只读发布。该功能不更改任何引擎的投资 prompt、schema、validator 或 Paper 执行规则。
