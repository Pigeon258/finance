# VISUAL-THEME-08 整体验收与生产发布

## 目标

完成主题系统的全流程、部署和生产验收，形成可重复发布、回滚和后续制作主题包的稳定基线。

## 依赖

`VISUAL-THEME-07`

## 范围

- 执行全部自动化质量门槛和财务核心回归。
- 验证 Compose 静态资源、持久主题库、只读容器、Caddy、CSP 和缓存。
- 演练主题导入、预览、启用、损坏回退、删除、备份恢复和应用升级。
- 完成主题包作者规范、部件契约、格式版本、升级和故障恢复文档。
- 在生产发布前创建备份并记录版本、主题格式和验收证据。

## 不包含内容

- 不建设在线市场、社区审核平台或自动更新服务。
- 不发布许可不明确的主题资产。
- 不跳过既有生产部署与备份恢复门槛。

## 验收命令

```powershell
uv run ruff check .
uv run pytest
uv run python manage.py check
uv run python manage.py check --deploy --settings=config.settings.production
docker compose config
```

同时使用 `powershell.exe` 执行 Windows 兼容验证，并在真实 Compose 环境检查主题静态资源、持久化和回退。

## 完成标准

- `FR-THEME-*` 和 15.9 验收项均有自动化或人工证据。
- 主题发布不改变财务完整性结果和业务备份恢复能力。
- 生产可快速恢复 `safe-default` 或上一应用版本。

## 建议提交

`chore: validate and release visual theme system`
