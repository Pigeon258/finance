# Personal Finance 开发约束

## 权威输入

- 开发前必须阅读 `docs/requirements.md` 与 `docs/system-design.md`。
- 两份基线文档不得在实现任务中顺手修改；发现冲突时，先在对应任务中记录决策，再单独走设计变更。
- `tasks/README.md` 是实施顺序与状态的唯一索引；具体范围以对应任务文档为准。

## 第一版边界

- 采用 Python 3.13、Django 5.2 LTS、Django Templates、HTMX、Bootstrap 5、ECharts、PostgreSQL 17、Gunicorn、Caddy、Docker Compose。
- 保持服务端渲染的模块化单体；不得引入 React、Vue、FastAPI、Redis、Celery、消息队列、微服务或独立 REST API 工程。
- 优先完成最小可运行骨架与核心账本，再开发信用卡、分期、预算、导入、报表和备份。
- 不实现 `docs/requirements.md` 4.2 与 `docs/system-design.md` P2 所列功能。
- P1 功能仅在对应任务明确列入时实现，不借任务扩大范围。

## 财务正确性

- 所有业务金额在 Python 中使用 `Decimal`，模型使用 `DecimalField(max_digits=14, decimal_places=2)`；禁止 `float` 参与金额计算。
- `Transaction + TransactionEntry` 是资金事实唯一来源；不得新增可修改的缓存余额或重复消费事实。
- 跨模块写操作只能调用目标模块 `services`；查询通过 `selectors`；不得从其他模块直接写内部模型。
- 关键写操作使用数据库事务；并发敏感操作使用数据库约束、状态复核和必要的行锁保证幂等。
- 保持 `docs/system-design.md` 第 54 节全部不变量。涉及账务口径的提交必须包含正向、反向、边界和回归测试。
- 已进入正式关系的数据不得破坏性删除；使用停用、作废、反向修正或替代交易。

## 实施与提交

- 每次只实施一个任务文档定义的范围；先完成其前置任务。
- 每个任务应形成一个可审查提交，确需拆分时只拆成一组紧密相关提交。
- 数据库迁移与对应模型/服务在同一任务中提交；不得提交无消费者的前瞻性抽象。
- 页面必须在无 HTMX 时仍可完成核心流程；HTMX 只增强局部交互。
- 真实账单、生产备份、`.env`、密钥和敏感日志不得进入仓库。

## 测试门槛

- 优先运行任务文档列出的窄测试，再运行受影响模块测试；账本规则变更还需运行核心集成测试。
- 默认质量门槛：`ruff check .`、`pytest`、`python manage.py check`。
- 部署相关任务另需 `python manage.py check --deploy --settings=config.settings.production`、Compose 配置校验、健康检查和恢复演练。
- 测试数据中的金额一律使用字符串构造 `Decimal`。

## 未决事项处理

- `tasks/README.md` 中“阻塞开发”的问题必须在进入受影响任务前，通过需求/设计负责人给出书面结论；实现者不得自行改变基线。
- “可以在具体任务中决定”的问题可在任务内做最小决策，并在测试或任务记录中固化。
- 任何会改变统计口径、账期归属、分期入账时点、备份格式或安全边界的决定，都不属于普通实现细节。
