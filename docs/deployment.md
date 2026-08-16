# 自托管部署与恢复

本文档说明 Personal Finance 的单机 Docker Compose 部署方式。示例中的密码、域名和密钥均需替换，禁止直接用于生产环境。

## 1. 前置条件

- Linux、Docker Engine 与 Compose v2；
- 指向服务器的域名，防火墙只开放 SSH、80 和 443；
- 非 root 部署用户和仅该用户可访问的部署目录；
- 建议至少 2 GB 内存和 20 GB 磁盘；1 GB 环境应配置 swap 并观察备份峰值；
- 独立保存的数据库备份主密钥。

仓库固定了基础镜像版本。升级依赖或镜像前应检查兼容性，并重新执行部署验证和恢复演练。

## 2. 配置与密钥

以下示例使用 `/opt/personal-finance`：

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

编辑 `.env` 中的域名、ACME 邮箱和时区。`backup_master_key` 必须额外保存在密码管理器或离线介质中；密钥与备份文件不能只存在于同一服务器。

## 3. 首次部署

```bash
set -a; . ./.env; set +a
sh deploy/first-deploy.sh
export APP_DOMAIN
sh deploy/verify-deployment.sh
```

部署后确认：

1. HTTP 自动跳转到 HTTPS；
2. 未登录用户无法访问业务页面；
3. 首页、信用卡账期、CSV 导出和 `.pfbackup` 下载可用；
4. `/health/*` 经 Caddy 返回 404；
5. 数据库 5432 和 Gunicorn 8000 没有宿主机端口映射。

## 4. 自动备份

`backup` 服务按应用时区执行：

- 每日 02:30 创建数据库备份，保留最近 7 份；
- 周日 03:00 创建周备份，保留最近 4 份；
- 每日清理过期会话、登录尝试、导入临时记录和过期恢复前备份；
- 部署前和手工备份不自动轮换。

数据库备份会执行 `pg_dump --format=custom`、AES-256-GCM 加密、解密验证、SHA-256 核对和 `pg_restore --list`。明文 dump 只写入容器 tmpfs，并在结束时删除。

手工创建备份：

```bash
docker compose run --rm backup python manage.py database_backup --kind manual
```

应监控 `docker compose logs backup`、备份失败记录和磁盘剩余空间。日志不得记录请求正文、账单内容、密码、Cookie 或连接字符串。

## 5. 升级

```bash
git fetch --tags
git checkout <reviewed-version>
set -a; . ./.env; set +a
sh deploy/upgrade.sh
sh deploy/verify-deployment.sh
```

升级脚本会构建镜像、创建加密数据库备份、启用维护模式、执行迁移和完整性检查，然后启动服务。失败时应保持维护模式，先判断需要镜像回滚、主题恢复还是数据库恢复。

升级前请记录当前版本、镜像、部署前备份和活动主题。升级后执行：

```bash
docker compose exec -T web python manage.py check_financial_integrity
docker compose exec -T web python manage.py check_theme_integrity --strict
```

再检查登录、首页、当前信用卡账期、账单导入、主题库和手机布局。

### 5.1 运行时主题卷

生产 Compose 使用 `theme_data` 命名卷。Web 以读写方式挂载 `/app/var/themes`，Caddy 以只读方式挂载 `/srv/themes`。应用升级和镜像回滚不会删除该卷，请勿执行 `docker compose down -v`。

主题资产不进入 `.pfbackup` 或数据库备份。请在独立可信位置保存主题原始 ZIP、许可文件和 SHA-256。主题卷丢失时，系统应回退到 `safe-default`；恢复外观时必须重新导入可信主题包，不能绕过校验直接复制运行时文件。

## 6. 回滚

没有不兼容数据库迁移时，可使用保留的旧镜像：

```bash
sh deploy/rollback.sh <old-web> <old-caddy> <old-maintenance>
```

如果版本包含不兼容迁移，不要假设 Django migration 可以安全反向执行。应恢复升级前数据库备份，并使用与该备份兼容的应用镜像。

## 7. 数据库恢复

优先在隔离服务器或新数据库中演练恢复。原服务器恢复命令：

```bash
sh deploy/restore-database.sh db-deployment-YYYYMMDDTHHMMSSZ-ID.dump.enc
```

脚本会先创建新的安全备份，再停止公网服务。恢复使用 `pg_restore --single-transaction --exit-on-error`，失败不会提交部分数据库。

恢复后检查：

1. 所有者账号和登录；
2. 账户数量、主要余额和最近交易；
3. 退款、修正及信用卡账期关系；
4. 分期已入账与未入账关系；
5. 当月预算、计划现金流和导入幂等记录；
6. 首页净资金和未来事项；
7. `check_financial_integrity` 与部署验证结果。

恢复记录只保存版本、备份标识、加密文件 SHA-256、记录数量和检查结论，不记录财务金额、密码或密钥。

## 8. 灾难恢复

1. 准备与备份格式和数据库结构兼容的应用版本；
2. 创建空 PostgreSQL 服务并安全提供数据库密码与备份主密钥；
3. 将加密备份复制到 `backups/database/`；
4. 执行数据库恢复、迁移和财务完整性检查；
5. 启动 Web 与 Caddy，运行 `deploy/verify-deployment.sh`；
6. 删除所有临时明文，重新检查密钥和备份目录权限。

如果恢复后的 `theme_data` 缺少原活动主题，页面会回退到 last-known-good 或 `safe-default`，业务恢复不应因此失败。

## 9. 重启与故障处理

部署完成后应执行一次服务器重启演练，确认四个服务因 `restart: unless-stopped` 自动启动，并重新运行部署验证脚本。

若进程异常终止后维护模式残留，可在确认没有迁移或恢复任务运行后执行：

```bash
docker compose run --rm web python manage.py maintenance_mode disable
```

不要在仍有恢复任务运行时强制关闭维护模式。
