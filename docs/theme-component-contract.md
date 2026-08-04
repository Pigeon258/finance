# Personal Finance 主题组件契约

> 契约版本：1
> 对应任务：`VISUAL-THEME-03`

主题组件契约把业务模板和视觉主题隔开。业务模板负责语义、数据、权限、表单与链接；主题只能通过设计令牌和注册的 `data-pf-part` 部件改变表现。

## 注册部件

| 部件 | 语义 | 当前落点 |
|---|---|---|
| `app-shell` | 页面最外层应用壳 | `body` |
| `top-navigation` | 桌面主导航容器 | 侧栏 `aside` |
| `navigation-menu` | 主导航或子导航链接组 | 导航 partial |
| `page-header` | 页面或工作区标题区域 | 顶栏、页面标题 |
| `content-panel` | 当前页面主要内容 | `main` |
| `metric-card` | 单个关键财务指标 | 仪表盘指标卡 |
| `action-group` | 一组同级快捷操作 | 快捷操作导航 |
| `form-panel` | 筛选或业务表单区域 | 登录、筛选表单 |
| `data-table` | 财务数据表格 | 交易和仪表盘表格 |
| `status-badge` | 文字与颜色共同表达的状态 | 风险状态 |
| `message-banner` | Django 消息组 | 消息 partial |
| `chart-panel` | ECharts 图表承载区域 | 报表页图表 |
| `auth-panel` | 登录等认证区域 | 登录页 |
| `modal-panel` | 需要显式确认的覆盖层 | 后续按实际消费者启用 |

## 稳定性规则

1. 同一主题格式主版本内，不删除或改变已发布部件的业务语义。
2. 主题不得依赖元素在 DOM 中的偶然层级、Django 自动生成类名或字段 ID。
3. 主题不得改变表单 `action`、`method`、CSRF、字段 `name`、链接目标和权限判断。
4. 财务状态同时保留文字，不能只用颜色、图标或动画表达。
5. 没有主题 CSS 和 JavaScript 时，HTML 文档顺序仍与操作顺序一致。

## 样式层次

```text
Bootstrap 5.3.8 基础层
→ static/css/app.css 结构与组件层
→ safe-default 或活动主题令牌层
```

`app.css` 必须为所有视觉令牌提供安全回退值。`safe-default` 是不可删除的内置主题，不能依赖图片、字体下载或 JavaScript。

## 模板复用

- `components/primary_navigation.html`：桌面侧栏；
- `components/navigation_links.html`：桌面和手机共享链接；
- `components/messages.html`：统一 Django 消息语义与视觉。

新增跨页面组件时优先增加 partial，并在本文件登记部件名称和稳定状态属性。
