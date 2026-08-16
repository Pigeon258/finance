# 理财管理模块设计

> 状态：已确认并随 `v0.3.0` 发布。
> 基线约束：`docs/requirements.md`、`docs/system-design.md` 仍是第一版核心账本基线；本模块作为新增业务模块，不改变既有账务事实和统计口径。

## 1. 目标

为当前单用户系统增加轻量理财管理：

- 理财资产与“当前净资金”分离。
- 日常账户与理财账户可以相互转换。
- 展示处于理财状态的金额、收益和收益率。
- 支持余额宝（天弘余额宝货币基金 `000198`）收益率的服务端抓取。
- 无法自动抓取的产品支持手工估值和收益录入。

第一版目标用户场景：

- 可投入金额通常在 1000 元以内。
- 资产以存款、余额宝/货币基金为主。
- 关注总资产、收益、累计收益率和年化收益率。

## 2. 财务口径

### 2.1 当前净资金

```text
当前净资金 = 日常流动资产 - 当前信用卡负债
```

理财账户不属于日常流动资产。

### 2.2 理财资产

```text
理财总市值 = 所有启用理财账户的 current_value 之和
理财本金/成本 = 关联核心账户余额（日常转入转出的净额）
理财收益 = 理财总市值 - 理财本金
```

### 2.3 收益分类

| 类型 | 是否改变理财市值 | 是否进入日常月度收入 |
|---|---|---|
| 余额宝/基金收益继续留在理财账户 | 是 | 否，只计入理财收益 |
| 收益实际到账日常账户 | 是/按具体操作 | 是，创建核心账本收入 |
| 手工估值上涨/下跌 | 是 | 否 |
| 日常账户转入理财 | 是 | 否 |
| 理财转回日常账户 | 是 | 否 |

## 3. 模型

### 3.1 核心账户扩展

`accounts.Account` 增加账户类型 `WEALTH`，显示为“理财账户”。

- `liquid_assets` 选择器排除 `WEALTH`。
- 收入、支出、信用卡消费等日常收支表单排除 `WEALTH`。
- 转账表单仍可使用 `WEALTH`，用于日常账户与理财账户转换。

### 3.2 WealthAccount

- `name`
- `account_type`：`DEPOSIT`、`MONEY_FUND`、`BOND_FUND`、`INDEX_FUND`、`OTHER`
- `institution`
- `core_account`：一对一关联 `Account`，账户类型必须为 `WEALTH`
- `current_value`
- `valuation_date`
- `fund_code`：可选，余额宝固定为 `000198`
- `auto_fetch_enabled`
- `seven_day_annual_yield`
- `per_ten_thousand_income`
- `last_sync_at`
- `is_active`
- `sort_order`
- `note`

### 3.3 WealthFlow

- `wealth_account`
- `flow_type`：`TRANSFER_IN`、`TRANSFER_OUT`、`INCOME`、`VALUATION`
- `amount`
- `occurred_on`
- `related_transaction`：关联核心账本交易，可选
- `note`

## 4. 页面

- `/wealth/`：理财总览
- `/wealth/accounts/new/`：新增理财账户
- `/wealth/accounts/<id>/edit/`：编辑
- `/wealth/accounts/<id>/valuation/`：手工估值
- `/wealth/accounts/<id>/income/`：记录理财收益
- `/wealth/accounts/<id>/sync-yuebao/`：余额宝自动同步
- `/wealth/transfers/in/`：日常账户转入理财
- `/wealth/transfers/out/`：理财转回日常账户

首页增加“理财资产”卡片：

- 理财总市值
- 本月理财收益
- 累计收益率

## 5. 余额宝自动同步

数据源：东方财富基金数据脚本 `fund.eastmoney.com/pingzhongdata/000198.js`。

抓取字段：

- `Data_millionCopiesIncome` 最后一期：每万份收益
- `Data_sevenDaysYearIncome` 最后一期：七日年化收益率

约束：

- 只在服务器端抓取，不允许浏览器跨域脚本。
- 超时 10 秒，失败时保留旧值并提示用户手工估值。
- 仅支持 `fund_code=000198` 且用户主动点击同步。
- 该接口非官方承诺 API，后续若失效可关闭自动同步并回退手工估值。

## 6. 收益与收益率

```text
累计收益 = 当前市值 - 关联核心账户余额
累计收益率 = 本金不为 0 时：累计收益 / 本金
年化收益率 = 本金和持有天数有效时：累计收益 / 本金 / 持有天数 * 365
本月理财收益 = 本月 WealthFlow.INCOME 金额之和
```

## 7. 不包含

- 股票/基金实时行情
- 券商、支付宝自动同步
- 税务和复杂持仓
- 智能投顾
