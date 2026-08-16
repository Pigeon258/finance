# 主题包制作、升级与恢复指南

Personal Finance 的“插件接口”是版本化、声明式主题包，不是任意代码插件。主题可以替换设计令牌、注册组件样式、背景艺术和图表外观，但不能执行 Python、JavaScript、Django 模板、系统命令或远程请求。

当前应用版本为 `0.3.0`，主题格式版本和组件契约版本均为 `1`。完整字段与 Safe CSS 规则分别见 `docs/theme-package-contract.md` 和 `docs/theme-component-contract.md`。

## 1. 目录与命名

```text
my-theme/
├── manifest.json
├── theme.json
├── theme.css
├── preview.webp       # 可选，建议 16:9、小于 50 KiB
├── LICENSE.txt        # 使用第三方或可再分发素材时必需
└── assets/            # 可选，本地图片或 WOFF2
```

主题 ID 使用小写 ASCII、数字和连字符，例如 `calm-ledger`。ZIP 可以直接放上述文件，或只增加一层同名顶级目录；不得加入第二个主题、隐藏载荷或嵌套 ZIP。

## 2. 制作顺序

1. 从 `safe-default` 的 `theme.json` 和 `theme.css` 复制结构，不复制 Aurora 的原创图片。
2. 在 `theme.json` 中声明明暗外观、`--pf-*` 令牌、背景、注册部件、图表和无障碍能力。
3. `theme.css` 只使用组件契约登记的单一 `[data-pf-part="..."]` 选择器和允许的状态伪类。
4. 将每个非 `manifest.json` 文件的相对路径、字节数和 SHA-256 写入 `manifest.json`。
5. 将 `schema_version`、`contract_version` 固定为 `1`，`min_app_version` 设置为实际依赖的最低应用版本。
6. 保留素材来源和许可记录；不得复制其他项目中未明确授权的角色、图片、字体、商标和代码。

PowerShell 可用以下命令取得文件大小和哈希：

```powershell
Get-Item .\theme.json | Select-Object Length
(Get-FileHash .\theme.json -Algorithm SHA256).Hash.ToLowerInvariant()
Compress-Archive -Path .\my-theme\* -DestinationPath .\my-theme.zip
```

`manifest.json` 不登记自身。修改任一资源后必须同步更新其 `size` 和 `sha256`。

## 3. 本地验收

1. 登录后打开“系统设置 → 管理主题库”。
2. 导入 ZIP；导入成功只进入主题库，不会自动启用。
3. 临时预览桌面 1440×900 和手机 390×844，覆盖首页、交易、表单、报表、设置和主题库。
4. 检查键盘焦点、对比度、减少动效、背景关闭和图表下方的数据表。
5. 点击“明确启用”，再运行：

```powershell
uv run python manage.py check_theme_integrity --strict
uv run pytest apps/core/tests/test_theme_runtime.py apps/core/tests/test_theme_library.py apps/core/tests/test_visual_quality.py -q
```

安全导入会检查 ZIP 限额、路径、清单、哈希、UTF-8、Safe CSS、图片和 WOFF2 实际内容；不要以修改扩展名的方式绕过格式约束。

## 4. 版本与升级

- 只修改视觉且不改变包结构时递增主题补丁版本；新增兼容能力时递增次版本。
- 需要新的主题格式或组件契约时，先升级应用文档和运行时，再提高 `schema_version` 或 `contract_version`；旧应用会失败关闭。
- 相同 ID、相同内容可重复导入；相同 ID、不同内容不会覆盖。升级主题时使用新 ID，或先安全切换并删除旧运行时主题后再导入。
- 应用升级会保留 Compose 的 `theme_data` 卷。升级脚本在退出维护模式前执行严格主题完整性检查。

## 5. 故障恢复

- 预览失败：结束预览，活动主题和 last-known-good 不变。
- 启用失败：修复主题包后重新导入；系统保留原活动主题。
- 当前主题损坏：主题选择器依次尝试 last-known-good 和不可删除的 `safe-default`。
- 需要立即恢复：在主题库点击“一键恢复安全默认”，或执行 `python manage.py check_theme_integrity` 确认回退结果。
- 运行时主题卷丢失：业务数据和主题偏好仍可恢复，页面使用安全默认主题；从可信原始 ZIP 重新导入所需主题。
- 应用回滚：使用发布前数据库备份和上一版本镜像；不要假设 Django migration 可以安全反向执行。

主题资产不进入 `.pfbackup` 或数据库备份，这是明确的安全与体积边界。主题作者和管理员应在独立可信位置保存原始 ZIP、许可文件及其 SHA-256。
