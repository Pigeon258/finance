# WEALTH-01 理财账户基础

## 目标

建立财富模块基础模型、核心账户类型和理财账户管理页面。

## 范围

- 新增 `apps.wealth`。
- 扩展 `Account.AccountType.WEALTH`。
- 日常净资金排除理财账户。
- `WealthAccount`、`WealthFlow` 模型和备份。
- 理财账户新增、编辑、停用和总览。

## 验收

- 理财账户不出现在日常流动资产。
- 页面登录保护。
- 备份恢复包含理财模型。
