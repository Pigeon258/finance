# QUICK-ITERATION-01 导航重复显示修复

## 目标

当基础结构样式暂时未加载时，移动导航默认失败关闭，避免桌面导航与移动导航同时显示；同时让生产部署验证覆盖基础结构样式，而不只检查主题样式。

## 快速迭代适用性

- 只影响页面壳层的显示降级和部署检查。
- 不改变账务事实、统计口径、表单提交、安全边界、数据模型或数据库迁移。
- 可通过回退本任务提交恢复，生产发布不需要数据回滚。

## 范围

- 为移动导航增加无 CSS 时的默认隐藏语义。
- 在窄屏断点显式恢复移动导航。
- 增加模板/CSS 回归测试。
- 将 `static/css/app.css` 的状态、媒体类型、缓存和 `nosniff` 纳入部署验证。

## 不包含内容

- 不调整主题视觉、导航信息架构或业务页面。
- 不修改需求基线和系统设计。
- 不执行数据库迁移或改变生产数据。

## 验收

```powershell
.\.venv\Scripts\ruff.exe check apps/core/tests/test_ui_components.py tests/test_deployment_configuration.py
.\.venv\Scripts\pytest.exe apps/core/tests/test_ui_components.py tests/test_deployment_configuration.py
.\.venv\Scripts\python.exe manage.py check
```

人工检查桌面宽度只显示侧栏导航，窄屏只显示“菜单”入口；禁用 `app.css` 时不得出现第二套导航控件。

## 发布与回滚

- 本任务符合低风险快速发布条件，使用 `deploy/quick-upgrade.sh` 只重建 Web/Caddy；脚本仍创建加密部署备份、保存三组旧镜像并执行应用检查。
- 发布后执行 `deploy/verify-deployment.sh` 和桌面/窄屏首页冒烟。
- 若页面壳层异常，使用脚本输出的 `quick-rollback-*` 三组镜像执行 `deploy/rollback.sh`；本任务没有数据库回滚步骤。

## 完成记录

- 2026-08-05 完成最小修复和回归测试，等待快速生产发布。
- Ruff 窄检查通过；相关测试 `15 passed`；Django system check 通过；`git diff --check` 通过。
- 在线只读复核时 `static/css/app.css` 已返回 200、`text/css`、一小时重新验证缓存和 `nosniff`，截图对应异常未持续存在。
