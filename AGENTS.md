# AGENTS.md

APP 自动化测试平台（Appium 移动端自动化：Vue3 + FastAPI + PostgreSQL + 独立 Agent）。

**设计文档是唯一权威**（API/DB 表结构/WS 协议）：`docs/APP自动化测试平台_详细架构实施方案.md`（V1.1，第 10 章为最终口径，与正文冲突以第 10 章为准）。

## 环境（Windows / PowerShell 5.1）
- **uv 不在默认 PATH**：新 shell 需先执行 `$env:Path = "$env:USERPROFILE\.local\bin;" + $env:Path`，否则所有 `uv run ...` 都会报"无法识别"
- PostgreSQL 18.6 原生安装（非 Docker）。`psql` 在 `D:\Program Files\pg\bin`（已加入用户 PATH，新开终端生效）。库 `test_platform`，用户 `dev` / 密码 `dev123`
- **Node 18.16**：不要用 `create-vite@9`、不要装 `sass`（均需 Node 20+）；前端 Vite 固定 5.x
- 本机无 Docker；后端无 Docker 化要求
- PowerShell 5.1 不支持 `&&`，链式用 `cmd1; if ($?) { cmd2 }`

## 后端 backend/（FastAPI + SQLAlchemy 2.0 async + asyncpg）
命令均需在 `backend/` 目录下执行：
- 启动：`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`（长驻进程，用 `Start-Process ... -WindowStyle Hidden` 后台启动后再 curl 验证）
- 迁移：`uv run alembic revision --autogenerate -m "..."` → 审阅生成的迁移 → `uv run alembic upgrade head`
- 测试：`uv run pytest tests/ -q`（pytest-asyncio `asyncio_mode=auto`）
- Lint：`uv run ruff check app/ tests/ --fix`（选 `E,F,W,I,UP,B`，忽略 `E501,B008`；B008 是 FastAPI 的 `Depends` 默认参数惯例，勿"修复"）
- 种子：`uv run python -m app.seed`（创建 admin / admin123）

关键点：
- `pyproject.toml` 中 `[tool.uv] package=false`：`app/` 是普通可导入包，运行必须 cwd=backend/，不存在 `src` 布局
- 目录：`app/api`（路由）、`app/models`（19 张业务表）、`app/core`（config/database/security）、`app/schemas`、`app/services`、`app/utils`、`alembic/`、`worker.py`（尚未创建）
- 权限依赖在 `app/api/deps.py`：`require_project_role("owner","admin")` 返回**元组 `(project, role)`**，必须解包 `project, role = perm`——最常见的接线 bug（曾把元组当 Project 用导致 AttributeError）
- 路由注册用 `app.include_router(router, prefix="/api")`。FastAPI 0.141 路由延迟解析，打印 `app.routes` 只见 `_IncludedRouter` 属正常

## 测试注意
- 测试直接连真实 dev 库（无独立测试库），数据会落库
- `tests/conftest.py`：每个测试前删除 `pytest_%` 用户及其项目/成员/token；每个测试后 `await engine.dispose()`——**asyncpg 连接不能跨 pytest 事件循环**，缺 dispose 会报 "Event loop is closed"
- 测试必须自建用户（如 `_register` helper），**不要依赖其他测试的执行顺序**
- JWT refresh token 必须含 `jti`（`security.py` 已处理）——同秒签发 token 会完全相同，造成 `refresh_tokens` 哈希重复

## 数据库
- 存在循环外键 `devices.locked_by_execution ↔ executions.device_id`（设计如此）。初始迁移已手工拆分为"先建表后 `op.create_foreign_key`"，改表/新迁移时注意建表顺序，否则 PG 报错
- 软删除用 `deleted_at`（projects/test_modules/test_elements/test_cases/test_suites），其余表硬删

## 架构要点（V1.1 §10，务必遵守）
- 进程职责三分：**FastAPI** = WS 网关 + 执行细节落库(execution_steps/assertions/logs) + 广播；**Worker** = 队列消费(SKIP LOCKED) + 设备原子锁 + 报告生成；**Agent** = Action/Assertion Registry 实际执行
- Worker 与 Agent **无直接 WS**：经 `/internal/ws/agents/{id}/send` 由 FastAPI 转发（`X-Internal-Token`）
- 执行状态机**全小写**：`queued / running / stopping / passed / failed / error / stopped / cancelled`
- Action/Assertion Registry 属于 agent 包，不属于 backend（backend/app/executor 目录可能废弃）

## Git
- 提交信息格式：`feat(backend|frontend|agent): Step N <内容>` 或 `chore: ...`
- 每完成一个 Step（lint + 测试通过）提交一次；仓库 local git 身份已配置（shijinsong），无需再配
