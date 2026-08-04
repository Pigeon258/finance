# Personal Finance 主题包契约

> 格式版本：1
> 组件契约版本：1
> 对应任务：`VISUAL-THEME-04`

主题包只包含声明式视觉资源，不是可执行插件。内置主题和运行时导入主题使用同一套清单、哈希、配置与 Safe CSS 校验；任何未知结构均失败关闭。

## 必需文件

```text
theme-id/
├── manifest.json
├── theme.json
└── theme.css
```

主题可按能力增加 `preview.webp`、`LICENSE.txt` 与 `assets/` 资源。除 `manifest.json` 外，每个普通文件都必须在清单中登记；不得存在符号链接、隐藏载荷、绝对路径、反斜杠路径或 `..` 路径。

## manifest.json

清单必须且只能包含：

- `schema_version`：当前固定为 `1`；
- `id`：小写 ASCII、数字和连字符，且必须与目录名一致；
- `name`：1～80 个字符；
- `version`、`min_app_version`：SemVer；
- `capabilities`：`tokens`、`background`、`safe-css`、`charts` 的无重复子集；
- `files`：每项精确包含 `path`、字节 `size` 和小写 SHA-256。

单文件上限 5 MiB，全部登记文件上限 20 MiB，清单最多 64 项。当前允许 JSON、CSS、TXT、WebP、PNG、JPEG 和 WOFF2；资源必须本地提供。

## theme.json

顶层必须且只能包含六个区段：

- `appearance`：`auto | light | dark`；
- `tokens`：名称为 `--pf-*` 的安全 CSS 值；
- `art`：`asset`、`focus`、`mode`、`overlay`、`safe_area`；
- `components`：仅引用已登记 `data-pf-part` 的结构化样式；
- `charts`：受限的 ECharts 主题对象；
- `accessibility`：高对比声明与减少动效策略。

焦点 `x`、`y` 包含边界且必须位于 `0..1`。背景资源只能引用清单内 `assets/` 文件。

## Safe CSS

`theme.css` 使用 `tinycss2` 解析为语法树后逐节点校验。选择器只能是单个已注册的 `[data-pf-part="..."]`，可追加 `hover`、`focus`、`focus-visible`、`active`、`disabled`、`checked` 以及 `before`、`after`。选择器列表中的每一项都独立校验。

允许颜色、背景、边框、排版、间距、阴影、透明度与受限动效属性，以及 `--pf-*` 令牌。禁止全局/通配选择器、未知属性、`!important`、全部 at-rule、解析错误、低于 `0.35` 的透明度和未登记资源 URL。URL 只能精确指向本主题清单内的 `assets/` 相对路径；远程地址、`javascript:`、HTML data URL 和外部字体加载均被拒绝。

## 运行时与回退

选择顺序固定为：活动主题 → last-known-good → `safe-default`。描述对象的缓存键由主题 ID、版本和清单内容修订值组成。数据库不可用、主题目录缺失、清单损坏、资源哈希不符或 CSS 拒绝时，请求仍使用安全默认组件样式，不影响核心表单与财务计算。

业务备份保存主题 ID 和外观偏好，不包含主题资产。恢复到未安装原主题的环境时保留偏好值用于审计，但页面安全回退；以后重新安装兼容主题即可重新选择。
