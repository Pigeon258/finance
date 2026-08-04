# 生产部署与恢复 Runbook

本文档只适用于 `docs/requirements.md` 与 `docs/system-design.md` 定义的单机 Docker Compose 第一版。不要把示例密钥用于生产。

## 1. 前置条件

- 受支持的 Linux 发行版、Docker Engine 与 Compose v2；
- 指向服务器的域名，防火墙只开放 SSH、80、443；
- 非 root 部署用户和 `/opt/personal-finance` 私有目录；
- 至少 2 GB 内存和 20 GB 磁盘更稳妥，1 GB 环境应配置 swap 并观察备份峰值。

仓库中的镜像基础版本已固定。升级这些版本必须单独审查并重新执行完整验收。

## 2. 配置与密钥

```bash
cd /opt/personal-finance
cp deploy/env.production.example .env
mkdir -p secrets backups/business backups/database
openssl rand -base64 48 > secrets/django_secret_key
openssl rand -base64 32 > secrets/database_password
openssl rand -base64 32 > secrets/backup_master_key
chmod 700 secrets
chmod 600 secrets/*
sudo chown -R 10001:10001 backups
chmod 700 backups backups/business backups/database
```

编辑 `.env` 中的域名、ACME 邮箱和时区。`backup_master_key` 必须额外保存到密码管理器或离线介质；它与备份文件不能只存在于同一服务器。

## 3. 首次部署

```bash
set -a; . ./.env; set +a
sh deploy/first-deploy.sh
export APP_DOMAIN
sh deploy/verify-deployment.sh
```

人工确认：HTTP 跳转 HTTPS、登录限制、首页、当前信用卡账期、CSV 和 `.pfbackup` 下载。`/health/*` 经 Caddy 应返回 404；数据库 5432 和 Gunicorn 8000 不得存在宿主机映射。

## 4. 自动备份

`backup` 服务按应用时区执行：

- 每日 02:30：保留最近 7 份 `db-daily-*.dump.enc`；
- 周日 03:00：保留最近 4 份 `db-weekly-*.dump.enc`；
- 每日清理过期会话、登录尝试、导入临时记录，并清理超过 7 天的恢复前业务备份；
- 部署前和手工备份不自动轮换，确认无用后由部署用户删除。

每次运维备份都会执行 `pg_dump --format=custom`、AES-256-GCM 加密、解密验证、SHA-256 核对和 `pg_restore --list`。明文 dump 只写入容器 `/runtime` tmpfs 并在结束时删除。

手工备份：

```bash
docker compose run --rm backup python manage.py database_backup --kind manual
```

应监控 `docker compose logs backup`、`BackupRun` 失败记录和备份目录剩余空间。日志不得复制请求正文、账单行、密码、Cookie 或连接字符串。

## 5. 升级

```bash
git fetch --tags
git checkout <reviewed-version>
set -a; . ./.env; set +a
sh deploy/upgrade.sh
sh deploy/verify-deployment.sh
```

脚本依次构建、创建部署前加密数据库备份、启用维护模式、迁移、启动、执行 deploy check、财务完整性和严格主题完整性检查。失败时不要直接关闭维护模式，应先判断是镜像回滚、主题恢复还是数据库恢复。

### 5.1 运行时主题卷

生产 Compose 使用命名卷 `theme_data`：Web 以读写方式挂载到 `/app/var/themes`，Caddy 以只读方式挂载到 `/srv/themes`。主题导入临时文件只进入 Web 的 `/app/runtime/theme-imports` tmpfs。应用升级和镜像回滚不会删除该卷；不要执行 `docker compose down -v`。

主题资产明确不进入 `.pfbackup` 或数据库备份。请在独立可信位置保存导入过的原始 ZIP、许可和 SHA-256。若主题卷丢失，先确认页面已回退 `safe-default`，再从可信 ZIP 重新导入；不得从未知运行时目录直接复制文件绕过校验。

升级前记录：完整 Git SHA/标签、上一组三个镜像、部署前加密数据库备份、当前活动主题、last-known-good、主题格式 `1` 和组件契约 `1`。升级后执行：

```bash
docker compose exec -T web python manage.py check_theme_integrity --strict
```

并人工检查主题库、桌面/手机首页、当前账期、主题预览与一键恢复安全默认。运行时主题资源只允许固定 `/themes/` 路径中的 CSS、图片和 WOFF2；Caddy 必须返回 `nosniff` 与不可变缓存头，未知资源返回 404。

## 6. 回滚

没有不兼容数据库迁移时，传入仓库或私有 registry 中保留的三个旧镜像：

```bash
sh deploy/rollback.sh <old-web> <old-caddy> <old-maintenance>
```

存在不兼容迁移时不要假设 Django migration 可安全反向执行，按下一节恢复部署前数据库备份，并同时使用与该备份应用版本兼容的镜像。

## 7. 运维数据库恢复演练

优先在隔离服务器或新数据库演练。原服务器原地恢复命令：

```bash
sh deploy/restore-database.sh db-deployment-YYYYMMDDTHHMMSSZ-ID.dump.enc
```

脚本先创建新的安全备份，再停止公网服务。恢复使用 `pg_restore --single-transaction --exit-on-error`；失败不会提交部分数据库。成功后执行迁移、清会话和财务完整性检查。

恢复后人工核对：

1. 管理员密码和登录；
2. 账户数量及主要余额；
3. 最近交易、退款和修正关系；
4. 当前信用卡账期与剩余应还；
5. 分期已入账/未入账关系；
6. 当月预算、计划现金流和导入幂等记录；
7. 首页净资金和未来 30 天事项。

在验收记录中保存日期、应用提交、备份文件名、加密文件 SHA-256、各类记录数和检查结论，不记录具体财务金额、密码或密钥。

## 8. 灾难恢复

1. 部署与备份头中 `app_version`、`schema_version` 兼容的镜像；
2. 创建空 PostgreSQL 服务并安全提供数据库密码与备份主密钥；
3. 将加密备份复制到 `backups/database/`；
4. 使用 `database_restore` 恢复，运行迁移和 `check_financial_integrity`；
5. 启动 Web/Caddy，执行 `deploy/verify-deployment.sh` 和人工核对；
6. 删除所有临时明文，重新确认 secrets 和备份目录权限。

恢复数据库后，若保存的活动主题在 `theme_data` 中不存在，页面应回退 last-known-good 或 `safe-default`，业务恢复不得因此失败。重新导入可信主题包并明确启用即可恢复外观。

## 9. 重启与故障处理

执行一次服务器重启演练，确认四个服务因 `restart: unless-stopped` 自动启动，并重新运行部署验证脚本。若进程被强制终止后维护模式残留，可在确认没有恢复或迁移进程后执行：

```bash
docker compose run --rm web python manage.py maintenance_mode disable
```

不要在仍有恢复任务运行时强制关闭维护模式。

## 10. 已完成的本地容器基线

2026-07-13，系统所有者在 Ubuntu 22.04 / WSL 2 中对提交 `b91668c` 完成了本地容器验收。四服务健康、端口隔离、本地 HTTPS 与安全头、真实运维备份、隔离数据库恢复、业务备份恢复、日志脱敏和 Docker Engine 重启恢复均通过，详细记录见 `docs/acceptance.md`。

该结果可作为目标服务器部署前的已验证基线，但不能证明公网 DNS/ACME、防火墙、磁盘权限、服务器重启和异地密钥保存已经在生产主机完成。生产部署仍需重新执行本 runbook 第 2～9 节。
