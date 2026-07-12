# 第一版开发路线图

本目录严格依据 `docs/requirements.md` v1.0 与 `docs/system-design.md` v1.0 拆分。计划先建立可运行骨架和账本事实，再逐层增加信用卡、预算、分期、分析、导入与运维能力。

## 文档一致性检查

### 阻塞开发

以下问题不阻塞骨架或核心账本，但会阻塞标注的后续任务；不得由实现者擅自改基线。

| 编号 | 问题 | 影响任务 | 需要的最小结论 |
|---|---|---|---|
| B-01 | “未来 30 天事项”要求展示分期应付款，但 `InstallmentItem` 只有 `due_month`，没有可判断 30 天窗口的具体日期。 | 07、10 | 明确分期到期日的来源，或确认第一版该项按月份近似展示。 |
| B-02 | 需求要求“JSON 完整备份/从 JSON 恢复”，设计规定用户文件为加密 `.pfbackup`，内部载荷含 JSON。是否还必须提供明文 JSON 下载未明确；明文方案又会削弱安全目标。 | 14 | 确认验收对象是“内部 JSON 的加密容器”，还是同时要求明文 JSON。 |
| B-03 | 需求要求分期消费记录“原始消费”并支持关联退款，设计规定创建分期时不创建全价交易、仅每期入账。尚未明确“原消费”在未入账/多期入账时具体关联计划、期次还是某笔交易。 | 07 | 明确分期退款的正式关联对象和累计退款上限口径。 |

### 可以在具体任务中决定

| 编号 | 问题 | 处理任务 |
|---|---|---|
| D-01 | `Merchant`、`Tag`、`TransactionTag`、`SystemPreference`、`LoginAttempt`、映射规则和常用交易模板仅给出职责，未给完整字段。按已列功能设计最小字段和约束。 | 01、03、12、13 |
| D-02 | 默认分类初始化数据、字段长度、表单布局、页面交互与显示顺序未固定。使用数据迁移和最小服务端页面，并以需求清单为准。 | 02、03、各 UI 任务 |
| D-03 | 系统时区可配置但没有默认值；运维备份示例使用 `Asia/Singapore`。开发默认值和生产配置来源需固化。 | 00、01、15 |
| D-04 | 登录限流的 IP 密钥哈希细节、全局计数并发实现、会话管理页面属于任务级细化。 | 01 |
| D-05 | 账期自动创建边界、跨月账单日、正式账单差异处理和溢缴款展示细节未完全展开。 | 06 |
| D-06 | 固定支出 occurrence 的生成窗口、幂等键及跳过/过期策略未固定。 | 08 |
| D-07 | 导入真实列名、编码、平台版本样本和模糊去重文本归一化规则需由脱敏样本驱动。 | 11、12 |
| D-08 | 自动备份调度器、维护模式实现、二进制备份头、Dockerfile/Caddyfile完整内容和 CI 工具未固定。 | 14、15 |
| D-09 | “编辑/删除错误交易”与设计的锁定/反向修正需按交易是否进入正式关系分别落地。 | 04、05 |

### 暂时无需处理

- 系统设计将最低服务器建议写为 2 GB，而需求允许 1～2 GB：这是推荐配置差异，不影响正确性。
- 系统设计增加平台分期、文件 ZIP 安全限制、运维备份验证等细化，均在既有范围内，不构成范围扩大。
- P2 项目（银行卡/信用卡解析、通知、PWA、对象存储、双因素、自动同步）不进入本路线图。
- 缓存、持久化统计表和复杂 PostgreSQL 调优在单用户数据规模下暂不需要。

## 依赖关系

```mermaid
flowchart LR
  T00["00 项目骨架"] --> T01["01 认证与设置"]
  T00 --> T02["02 账户与分类"]
  T02 --> T03["03 核心账本"]
  T03 --> T04["04 手动交易"]
  T04 --> T05["05 退款核对修正"]
  T04 --> T06["06 信用卡账期"]
  T04 --> T07["07 分期"]
  T03 --> T08["08 预算与计划现金流"]
  T07 --> T09["09 风险预测"]
  T08 --> T09
  T06 --> T09
  T09 --> T10["10 仪表盘与报表"]
  T03 --> T11["11 导入基础与解析器"]
  T11 --> T12["12 导入确认与规则"]
  T05 --> T12
  T10 --> T13["13 可用性与P1收口"]
  T12 --> T13
  T13 --> T14["14 导出备份恢复"]
  T14 --> T15["15 部署运维与整体验收"]
```

推荐按编号执行。01 与 02 可在 00 后并行；06、07、08 可在各自依赖满足后并行；11 可在核心账本稳定后与业务模块并行。合并前仍必须按依赖顺序集成。

## 任务状态表

| 任务 | 名称 | 依赖 | 建议提交 | 状态 |
|---|---|---|---|---|
| [00](00-project-foundation.md) | 项目骨架与测试基础 | 无 | `chore: bootstrap django project` | 已完成 |
| [01](01-auth-and-preferences.md) | 单用户认证、会话与设置 | 00 | `feat: add single-user authentication` | 已完成 |
| [02](02-accounts-and-categories.md) | 账户、分类与初始化 | 00 | `feat: add accounts and categories` | 已完成 |
| [03](03-core-ledger.md) | 核心账本模型与原子服务 | 02 | `feat: implement core ledger` | 已完成 |
| [04](04-manual-transactions.md) | 手动交易主流程 | 01、03 | `feat: add manual transaction flows` | 待开始 |
| [05](05-refunds-reconciliation-corrections.md) | 退款、余额核对与修正 | 04 | `feat: add ledger correction flows` | 待开始 |
| [06](06-credit-card-billing.md) | 信用卡账期与还款分配 | 04 | `feat: add credit card billing` | 待开始 |
| [07](07-installments.md) | 商品分期与预算承诺 | 04；B-01/B-03 | `feat: add installment plans` | 阻塞于决策 |
| [08](08-budgets-and-planned-cashflows.md) | 预算、储备与计划现金流 | 03 | `feat: add budgets and planned cash flows` | 待开始 |
| [09](09-risk-and-forecasting.md) | 偿还能力与未来预测 | 06、07、08 | `feat: add financial risk calculations` | 待开始 |
| [10](10-dashboard-and-reports.md) | 仪表盘与统计报表 | 09 | `feat: add dashboard and reports` | 待开始 |
| [11](11-import-foundation-and-parsers.md) | 导入隔离区与平台解析器 | 03 | `feat: parse alipay and wechat bills` | 待开始 |
| [12](12-import-review-and-confirmation.md) | 映射、去重与幂等确认 | 05、11 | `feat: add bill import review flow` | 待开始 |
| [13](13-usability-and-p1-closure.md) | 模板、复制、会话和阈值收口 | 10、12 | 一组紧密相关提交 | 待开始 |
| [14](14-export-backup-and-restore.md) | CSV、业务备份与恢复 | 13；B-02 | `feat: add encrypted backup and restore` | 阻塞于决策 |
| [15](15-deployment-operations-and-acceptance.md) | Compose、自动备份、完整性与验收 | 14 | 一组运维提交 | 待开始 |

## 每个任务的共同完成门槛

1. 任务范围和“不包含内容”均被遵守。
2. 数据迁移、服务、选择器、表单/视图和测试在同一审查单元内。
3. 所有金额使用 `Decimal`，核心写操作具有事务与约束保护。
4. 任务列出的测试通过，且未破坏已完成任务的集成测试。
5. 文档中的完成标准可由自动测试或明确的人工验收步骤证明。
