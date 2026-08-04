# VISUAL-THEME-04 主题运行时与包格式

## 目标

实现版本化、可验证、可回退的主题注册和渲染运行时，为内置主题与后续导入主题提供同一契约。

## 依赖

`VISUAL-THEME-03`

## 范围

- 实现主题描述类型、注册器、选择器和模板上下文。
- 固化 manifest、结构化配置、能力标识和兼容检查。
- 实现设计令牌、背景艺术、注册部件和 ECharts 主题输出。
- 为 `SystemPreference` 增加活动主题、last-known-good、外观和动效偏好。
- 实现 Safe CSS 语法树校验及作用域白名单。
- 实现内容修订值、缓存键和 `safe-default` 回退。

## 不包含内容

- 不开放任意 JavaScript、Django 模板或 Python 插件。
- 不实现用户 ZIP 上传和主题库管理 UI。
- 不允许远程主题资源或 CDN。

## 测试要求

- 清单和配置正向、边界、未知版本和不兼容版本测试。
- Safe CSS 选择器、属性、URL 和解析逃逸负向测试。
- 活动主题损坏、资源缺失、数据库不可用和 last-known-good 回退测试。
- 备份恢复到缺少主题的环境时回退测试。

## 完成标准

- 内置主题和未来导入主题使用同一运行时。
- 主题失效不会阻断请求或核心表单。
- 主题选择不改变任何财务查询和写操作结果。

## 建议提交

`feat: add secure theme runtime and package contract`
