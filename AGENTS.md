# AGENTS.md

APP 自动化测试平台（Appium 移动端自动化：Vue3 + FastAPI + PostgreSQL + 独立 Agent）。

**设计文档是唯一权威**（API/DB 表结构/WS 协议）：`docs/APP自动化测试平台_详细架构实施方案.md`（V1.1，第 10 章为最终口径，与正文冲突以第 10 章为准）。

## 环境（Windows / PowerShell 5.1）
- **当前仅为开发环境，项目尚未上线**：数据库结构、迁移和测试数据调整无需兼容历史数据；除非用户另行要求，不为旧数据保留兼容分支
- **uv 不在默认 PATH**：新 shell 需先执行 `$env:Path = "$env:USERPROFILE\.local\bin;" + $env:Path`，否则所有 `uv run ...` 都会报"无法识别"
- PostgreSQL 18.6 原生安装（非 Docker）。`psql` 在 `D:\Program Files\pg\bin`（已加入用户 PATH，新开终端生效）。库 `test_platform`，用户 `dev` / 密码 `dev123`
- **Node 18.16**：不要用 `create-vite@9`、不要装 `sass`（均需 Node 20+）；前端 Vite 固定 5.x
- 本机无 Docker；后端无 Docker 化要求
- PowerShell 5.1 不支持 `&&`，链式用 `cmd1; if ($?) { cmd2 }`

## 后端 backend/（FastAPI + SQLAlchemy 2.0 async + asyncpg）
命令均需在 `backend/` 目录下执行：
- 启动：`uv run uvicorn app.main:app --host 127.0.0.1 --port 8001`（**端口 8001**，本机 8000 被 C-Lodop 打印服务占用；长驻进程用 `Start-Process ... -WindowStyle Hidden` 后台启动，或用 `start-backend.ps1 -NoReload`）
- Worker 默认以 `WORKER_MODE=embedded` 随 FastAPI lifespan 启动，无需额外进程；切换为 `WORKER_MODE=external` 后可运行 `uv run python worker.py --worker-id worker-001 --enable-scans`（可用 `start-worker.ps1`，扫描任务仅一个外部实例启用）
- 迁移：`uv run alembic revision --autogenerate -m "..."` → 审阅生成的迁移 → `uv run alembic upgrade head`
- 测试：`uv run pytest tests/ -q`（pytest-asyncio `asyncio_mode=auto`）
- Lint：`uv run ruff check app/ tests/ worker.py scripts/ --fix`（选 `E,F,W,I,UP,B`，忽略 `E501,B008`；B008 是 FastAPI 的 `Depends` 默认参数惯例，勿"修复"）
- 类型：`uv run pyright`（等价 `uv run pyright app tests worker.py scripts`），须 0 errors。配置在 `backend/pyrightconfig.json`：`standard` 模式 + 范围 `app/ tests/ worker.py scripts/`；`tests/` 通过 executionEnvironments 单独放宽 `reportOptionalMemberAccess`（用例中 `db.get()`/`db.scalar()` 取到的行由前置断言/fixture 保证存在，取到 None 会立即 AttributeError，不会静默通过）。**注意：`backend/pyrightconfig.json` 优先于 `pyproject.toml` 的 `[tool.pyright]`，改配置只改前者**
- 种子：`uv run python -m app.seed`（创建 admin / admin123）
- 清理：`uv run python scripts/cleanup_reports.py [--dry-run]`（按保留天数删报告/截图/日志，Worker 每日 3 点自动执行）

关键点：
- `pyproject.toml` 中 `[tool.uv] package=false`：`app/` 是普通可导入包，运行必须 cwd=backend/，不存在 `src` 布局
- 目录：`app/api`（路由）、`app/models`（19 张业务表）、`app/core`（config/database/security）、`app/schemas`、`app/services`（execution/worker/report/cleanup）、`app/ws`（WS 网关）、`app/templates/reports`（报告 HTML 模板）、`scripts/`、`worker.py`、`start-backend.ps1`/`start-worker.ps1`
- 权限依赖在 `app/api/deps.py`：`require_project_role("owner","admin")` 返回**元组 `(project, role)`**，必须解包 `project, role = perm`——最常见的接线 bug（曾把元组当 Project 用导致 AttributeError）
- 路由注册用 `app.include_router(router, prefix="/api")`。FastAPI 0.141 路由延迟解析，打印 `app.routes` 只见 `_IncludedRouter` 属正常

## Agent agent/（独立 uv 环境）
- 启动：在 `agent/` 下 `uv run python main.py --config config.yaml`（或 `start-agent.ps1`）；`config.yaml.example` 为模板，需填 `server/agent_key/agent_id`
- 驱动：`driver: mock`（默认，本地联调）/ `appium`（真实 Appium，需 `pip install 'agent[appium]'`）
- 测试：`uv run pytest tests/ -q`；Lint：`uv run ruff check .`
- 上报设备：mock 模式用 `config.yaml` 的 `mock_devices`；appium 模式走 `adb devices`

## 前端 frontend/
- 启动：`npm run dev`（vite 代理 `/api` 与 `/ws` → 127.0.0.1:8001）
- 构建：`npm run build`（vue-tsc + vite，TS 类型即校验）
- WS 封装在 `src/composables/useExecutionSocket.ts`（VueUse useWebSocket + 自动重连 + 心跳）

## 测试注意
- **测试跑在独立库 `test_platform_test`，不碰开发库 `test_platform`**：`tests/conftest.py` 在导入任何 `app.*` 前把 `DATABASE_URL` 指向测试库，且库名不以 `_test` 结尾时直接 `RuntimeError` 拒绝运行（Step 12：杜绝污染开发库）。可用 `TEST_DATABASE_URL` 覆盖
- 会话开始时 conftest 只对**测试库**执行 `alembic upgrade head`；**开发库不会被自动升级**，所以 `uv run alembic check` 会在拉取含迁移的提交后失败——先手动跑一次 `uv run alembic upgrade head`
- `tests/conftest.py`：每个测试前删除 `pytest_%` 用户及其项目/成员/token；每个测试后 `await engine.dispose()`——**asyncpg 连接不能跨 pytest 事件循环**，缺 dispose 会报 "Event loop is closed"
- `tests/conftest.py` 每个测试前后会 `reset()` 三个 WS 全局单例（`agent_manager` / `execution_manager` / `profile_config_manager`），避免用例异常退出时残留 fake socket 污染后续用例
- 测试必须自建用户（如 `_register` helper），**不要依赖其他测试的执行顺序**
- JWT refresh token 必须含 `jti`（`security.py` 已处理）——同秒签发 token 会完全相同，造成 `refresh_tokens` 哈希重复
- **本机跑测试/构建要关掉沙箱的删除拦截**：报 `SAFE_DELETE_FAIL_CLOSED`（`SHFileOperationW 失败: 0x2`）或 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 时，加 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 前缀重跑，例如 `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest tests/ -q`。注意**不是** `CODEBUDDY_SAFE_DELETE_SANDBOX=0`（那只是"是否在沙箱内"的判定，改了没用）

## 数据库
- 存在循环外键 `devices.locked_by_execution ↔ executions.device_id`（设计如此）。初始迁移已手工拆分为"先建表后 `op.create_foreign_key`"，改表/新迁移时注意建表顺序，否则 PG 报错
- 软删除用 `deleted_at`（projects/test_modules/test_elements/test_cases/test_suites），其余表硬删

## 架构要点（V1.1 §10，务必遵守）
- 逻辑职责三分：**FastAPI** = WS 网关 + 执行细节落库(execution_steps/assertions/logs) + 广播；**Worker runtime** = 队列消费(SKIP LOCKED) + 设备原子锁 + 终态汇总(reports 统计行) + 扫描/每日清理（默认嵌入 FastAPI，也可独立进程）；**Agent** = Action/Assertion Registry 实际执行
- 报告：查看由前端渲染 `GET /api/reports/{id}/detail` 聚合数据；HTML 仅用户点下载时按需生成（`report_service.render_report_html`，截图 base64 内嵌、幂等缓存）
- Worker 与 Agent **无直接 WS**：经 `/internal/ws/agents/{id}/send` 由 FastAPI 转发（`X-Internal-Token`）
- 执行状态机**全小写**：`queued / running / stopping / passed / failed / error / stopped / cancelled`
- Action/Assertion Registry 属于 agent 包，不属于 backend；废弃的 `backend/app/executor/` 空目录已删除，禁止恢复后端执行 Registry
- **变量优先级（§10.6）固定为：执行参数 > 套件变量 > 用例变量 > 项目变量 > 全局变量**。无档案执行在 `app/repositories/worker.py::_materialize_unprofiled_tree` 里按此顺序逐层叠加（`base_map → case → suite → execution parameters`）；禁止"先复制完整映射再用低优先级覆盖高优先级"的写法，否则套件前后置与套件内用例会取到不同值
- **Agent WS 一条连接只允许注册一次**：`app/ws/routes.py` 注册成功后拒绝后续 `register`（回 PROTOCOL_ERROR，不断连）。`AgentConnectionManager.connect()` 对同一 socket 幂等，且"先替换映射、后关闭旧连接"
- **步骤/节点级状态同样受"终态不回退"保护**：`ws_handlers.py` 的 `_merge_step_status()` 按 `error > failed > stopped > skipped > passed` 合并（`cancelled` 与 `stopped` 同属中断终态）。禁止写成 `step.status = payload["status"]` 这类无条件赋值——重投/补报会把已落库的 failed 翻成 passed。判定类字段（`duration`/`actual_value`/`error_message`/`screenshot_path`）必须用 `_fill_if_present()`，**重投消息不带截图时不得清空已落库的失败证据**
- **WS 发送失败只在连接确实不可用时才摘除映射**：`managers._is_socket_gone()` 区分"连接已关闭"（Starlette `RuntimeError` / websockets `ConnectionClosed*` / `OSError`）与其它异常；序列化等瞬时错误把 socket 摘掉会让仍在线的 Agent 被误判 offline
- **前端列表请求必须有竞态保护**：`useListQuery` 用递增序号丢弃过期响应（旧写法只有"已卸载"布尔标志，快速翻页时旧页响应后到会覆盖新页数据）。改造时保留顺序返回的对照用例
- WS 网关对 socket 的依赖用 `app/ws/managers.py` 的 `BroadcastSocket` / `AgentSocket` Protocol 表达（只声明 `send_json(data)` 等必需能力），测试替身无需 `cast(WebSocket, ...)` 即可传入

## 套件级执行（以测试套件为执行与结果汇总单位，实施中）
- 执行结构分层：`Execution → ExecutionSuite → ExecutionCase → ExecutionStep → ExecutionAssertion`；套件前后置步也只存 `ExecutionStep`（`execution_suite_id` 非空、`execution_case_id` 为空）。
- `ExecutionStep.phase` 五值：`suite_setup / case_setup / case_main / case_teardown / suite_teardown`；检查约束保证父节点一致（套件阶段挂套件、用例阶段挂用例）。
- `ExecutionAssertion` 直接关联 `execution_step_id`，`assertion_order` 在同一步骤内排序。用例资产只保存 `steps`，每个步骤以 `assertions` 子数组配置动作成功后立即执行的断言；Agent 上报使用 `execution_step_id` + `execution_assertion_id`，不再存在用例级 `assertions` / `assertions_snapshot`。
- `ExecutionCase` 必填 `execution_suite_id` + `case_order`；同一 `case_id` 可在不同套件重复出现（取消跨套件去重）。
- 单用例无套件上下文时建**虚拟套件**（`ExecutionSuite.is_virtual=True, suite_id IS NULL`）；带 `context_suite_id` 时用指定套件规则。
- 解析器 `profile_resolver.ResolutionResult.suites: list[ResolvedSuite]`（不再扁平 cases，`cases` 仅为兼容属性）。节点覆盖以 `(suite_id, case_id)` 区分，避免共享用例跨套件污染；套件步跳过/覆盖用 `target_type='suite_step'` + `(suite_id, node_key)`。
- 报告三层统计：用例（`total/passed/...`）+ 套件（`suite_*`）+ 步骤（`step_*`），N/A 不入任何成功率分母；`not_applicable_suites` 单列。
- 执行详情/报告详情响应由扁平 `cases` 改为嵌套 `suites`（`load_suite_tree`）；报告服务保留 `cases`（`load_case_tree`）供 HTML/列表，`suites` 供分层展示。
- 执行状态优先级：`error > failed > stopped > skipped > passed`；套件前置失败则套件内用例 `skipped` 但套件后置仍执行。

## 编码测试规则（Step 门禁）
- **测试只在整个 Step 全部子任务完成后才执行**。一个 Step 内若包含多个子步骤任务（后端接口 / 前端页面 / 迁移 / 文档等），必须等所有子任务都实现完成，才运行该 Step 的完整测试（后端 pytest / Agent pytest / 前端 vitest+build / ruff / alembic check）。
- 禁止在 Step 中途对半成品跑完整测试集或提交；中途只做轻量语法自检（如 `ruff` 单文件、`vue-tsc` 单文件），不作为通过依据。
- 每个 Step 完成时的验收命令（按需组合，全部通过才提交）：
  - 后端：`uv run ruff check app/ tests/ worker.py scripts/` + `uv run pyright` + `uv run pytest tests/ -q` + `uv run alembic check`
  - Agent：`uv run ruff check .` + `uv run pytest tests/ -q`
  - 前端：`npm run test`（vitest）+ `npm run build`（vue-tsc + vite）
- **拉取含 `.env.example` 改动的提交后，先同步本地 `backend/.env`**：部分用例断言的就是配置上限（如 `tests/test_execution_timeout_limits.py` 断言 24h），`.env` 落后会让门禁以"代码 bug"的样子变红。典型：`MAX_EXECUTION_TIMEOUT` 必须与 `settings.max_execution_timeout` 的默认值一致
- Step 内子任务实现过程中发现的错误可当场修复，但**测试通过以整个 Step 完成后一次为准**；Step 间不共享半成品状态。
- 提交时机：Step 门禁全部通过后提交一次，提交信息 `feat(backend|frontend|agent): Step N <内容>`。

## Git
- 提交信息格式：`feat(backend|frontend|agent): Step N <内容>` 或 `chore: ...`
- 每完成一个 Step（lint + 测试通过）提交一次；仓库 local git 身份已配置（shijinsong），无需再配
