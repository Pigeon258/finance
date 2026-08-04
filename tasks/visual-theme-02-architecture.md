# VISUAL-THEME-02 主题架构与安全设计

## 目标

在现有 Django 模块化单体内设计一等主题系统，定义稳定组件契约、主题格式、Safe CSS、安全导入、回退、备份和部署边界。

## 依赖

`VISUAL-THEME-01`

## 范围

- 更新 `docs/system-design.md` 版本、技术栈、目录、CSP、备份和测试策略。
- 定义 `data-pf-part` 注册部件契约。
- 定义 `manifest.json`、`theme.json`、`theme.css` 和资产目录。
- 定义主题发现、预览、启用、last-known-good 和 `safe-default` 回退。
- 定义结构化配置、Safe CSS、ZIP 校验、静态资源和容器边界。

## 不包含内容

- 不实现注册器、解析器、数据迁移或管理页面。
- 不引入可执行插件模型或任意模板覆盖。
- 不改变现有部署拓扑和账务模块依赖。

## 验收要求

- 主题层无法修改表单提交目标、字段名、CSRF、权限和财务内容。
- 导入和启用均失败关闭，且安全默认主题始终可用。
- 业务备份缺少主题资产时仍可恢复。
- 设计包含视觉、安全、兼容、性能和生产验证门槛。

## 建议提交

`docs: design secure visual theme architecture`
