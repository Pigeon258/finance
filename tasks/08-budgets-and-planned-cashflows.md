# 08 预算、储备与计划现金流

## 目标

建立自然月预算、虚拟储备和固定支出/预计收入计划，并保证计划转实际不重复占用。

## 范围

- `MonthlyBudget`、`CategoryBudget`、`ReserveMovement`。
- `PlannedCashFlow`、`PlannedCashFlowOccurrence` 及幂等生成。
- 月度总预算、分类预算、储蓄目标、安全余量、复制上月预算。
- 固定支出与预计收入的创建、跳过和确认成正式交易。
- 普通实际消费、退款、固定支出和分期承诺的预算聚合。

## 不包含内容

- 风险分级、图表、外部提醒、复杂重复周期。

## 涉及模块

`budgets`、`ledger`、`installments`、`core`。

## 主要数据模型或接口

- 设计第 20～22 节模型和公式。
- `budgets.services` 负责预算/计划；确认 occurrence 调用 `ledger.services`。
- `budgets.selectors` 输出实际、承诺、储蓄目标、总占用和剩余。

## 实施步骤

1. 建模唯一约束与月份首日校验。
2. 实现预算 CRUD、复制和分类阈值。
3. 实现储备变动，明确其不创建 Entry。
4. 实现一次/月/年计划及 occurrence 幂等生成。
5. 实现计划确认与预算防重复，添加预算管理页面。

## 测试要求

- 月份唯一、分类预算唯一、复制幂等与 Decimal 精度。
- 退款冲减、储蓄目标占用但不算消费、储备不改余额。
- occurrence 确认前后总占用恒定；预计收入到账前不改余额。
- 80%/100% 边界以 Decimal 比较。

## 完成标准

- FR-BUD、FR-PLAN 和设计 22.6 防重复规则通过。
- 所有聚合均来自账本事实与未发生计划，无统计缓存表。

## 提交建议

单提交：`feat: add budgets and planned cash flows`
