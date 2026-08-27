# APP 自动化测试平台 — 详细项目架构实施方案

> **版本**: V1.1 (V1.0 + 评审增量修订)  
> **日期**: 2026-08-20  
> **目标**: 基于轻量化架构（Vue3 + FastAPI + PostgreSQL），融合可靠性、安全性与落地性优化，形成可直接进入开发阶段的技术实施方案。V1.1 补充了执行职责划分、Worker↔Agent 通信中转、元素快照补全、停止机制、设备原子锁、变量系统等评审缺口，详见第 10 章（与正文冲突处以第 10 章为准）。

---

## 1. 架构总览与核心变更

### 1.1 修订后的总体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户浏览器                                       │
│                    Vue 3 + Vite + Element Plus + Pinia                       │
│                         (WebSocket 自动重连机制)                              │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ HTTPS / WSS
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI 服务层                                   │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐    │
│  │   REST API 路由     │  │   任务调度中心      │  │   WebSocket 网关    │    │
│  │  Auth / Project     │  │  - 内存任务队列      │  │  /ws/executions    │    │
│  │  Case / Suite       │  │  - 状态机监控        │  │  /ws/agent         │    │
│  │  Execution / Report │  │  - 超时回收引擎      │  │  (Agent PSK 认证)   │    │
│  └────────────────────┘  └────────────────────┘  └────────────────────┘    │
└──────────────┬──────────────────────────────────────────────────────────────┘
               │ SQLAlchemy 2.x (Async)
               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PostgreSQL 14+                                 │
│  业务数据 │ 执行快照(JSONB) │ 执行日志 │ 设备锁 │ 任务队列表(轻量)            │
└─────────────────────────────────────────────────────────────────────────────┘
               │
               │ 任务分发 (独立进程消费)
               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Test Worker (独立进程)                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  TestExecutor Engine                                                │   │
│  │  ├── Action Registry (launch_app, click, input, swipe, ...)         │   │
│  │  ├── Assertion Registry (exists, text_equals, attr_contains, ...)   │   │
│  │  ├── 用例快照加载 (steps_snapshot / assertions_snapshot)             │   │
│  │  ├── 截图 & 日志收集                                                 │   │
│  │  └── 报告生成 (HTML + JSON)                                         │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ WebSocket (双向认证)
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Device Agent (用户侧电脑)                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  分发形式: 独立可执行文件 (.exe / .app / 二进制)                      │   │
│  │  技术栈: Python + PyInstaller (打包后零依赖)                         │   │
│  │  运行模式: 系统托盘常驻 / 后台服务 / Docker (可选)                    │   │
│  │  核心模块:                                                           │   │
│  │    - ConnectionManager (WebSocket + PSK 认证 + 心跳)                │   │
│  │    - DeviceManager (ADB / xcrun simctl 设备发现)                    │   │
│  │    - AppiumManager (Session 生命周期管理 + 异常兜底)                 │   │
│  │    - CommandExecutor (接收任务 → 执行 → 回传结果)                    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
        ┌──────────────────┐    ┌──────────────────┐
        │ Android 模拟器/真机 │    │ iOS 模拟器/真机   │
        │  ADB + Appium     │    │  WDA + Appium    │
        └──────────────────┘    └──────────────────┘
```

### 1.2 与原始设计的关键差异

| 维度 | 原始设计 | 修订后方案 | 理由 |
|------|----------|------------|------|
| **执行引擎** | FastAPI 直接启动 Worker | FastAPI 只入队，独立 Python 进程消费 | 防止长任务阻塞 API，支持多 Worker 扩展 |
| **Agent 交付** | 假设用户有 Python 环境 | PyInstaller 打包为独立可执行文件 | 测试人员无需安装 Python，降低落地阻力 |
| **Agent 认证** | 仅发送 agent_id | 预共享密钥 (PSK) + 注册握手 | 防止恶意 Agent 接入，窃取任务或伪造结果 |
| **执行可靠性** | 无全局超时机制 | 执行超时 + Agent 心跳 + 死锁自动恢复 | 防止僵尸执行和资源泄漏 |
| **报告可追溯性** | execution_cases 仅存 case_id | 增加 steps_snapshot / assertions_snapshot | 用例修改后历史报告仍可完整追溯 |
| **日志可靠性** | 仅通过 WebSocket 推送 | 先 INSERT 数据库，再 WebSocket broadcast | 刷新页面不丢日志，支持回放 |
| **设备并发** | 提到加锁但未明确实现 | 数据库乐观锁 (status + locked_by_execution) | 多 Worker/多实例场景下设备互斥有效 |
| **存储安全** | 直接暴露文件路径 | API 代理读取，禁止路径遍历 | 防止 ../../../etc/passwd 类攻击 |

---

## 2. 技术选型与版本锁定

### 2.1 前端

| 技术 | 版本 | 说明 |
|------|------|------|
| Vue | 3.4+ | Composition API + `<script setup>` |
| Vite | 5.x | 构建工具 |
| Element Plus | 2.7+ | UI 组件库 |
| Pinia | 2.1+ | 状态管理 |
| Vue Router | 4.x | 路由 |
| Axios | 1.7+ | HTTP 客户端 |
| VueUse | 10+ | 工具库（useWebSocket 等） |
| VueDraggable | 4.x | 套件用例拖拽排序 |

### 2.2 后端

| 技术 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 类型提示、性能、asyncio 优化 |
| FastAPI | 0.111+ | REST API + WebSocket + 自动文档 |
| SQLAlchemy | 2.0+ | ORM，使用 2.0 风格声明式模型 |
| Pydantic | 2.7+ | 数据校验与序列化 |
| Alembic | 1.13+ | 数据库迁移 |
| Uvicorn | 0.30+ | ASGI 服务器 |
| python-jose | 3.3+ | JWT 生成与校验 |
| passlib | 1.7+ | 密码哈希 (Argon2) |
| asyncpg | 0.29+ | PostgreSQL 异步驱动 |
| websockets | 12+ | FastAPI 底层 WebSocket 库 |
| APScheduler | 3.10+ | 定时任务（超时检查、心跳超时） |

### 2.3 自动化与 Agent

| 技术 | 版本 | 说明 |
|------|------|------|
| Appium-Python-Client | 4.x | Agent 端控制 Appium |
| PyInstaller | 6.x | 打包 Agent 为独立可执行文件 |
| ADB | 最新版 | Android 设备管理 |
| Appium Server | 2.x | 移动端自动化服务端 |
| WebDriverAgent (WDA) | 最新 | iOS 自动化底层驱动 |

### 2.4 数据库与基础设施

| 技术 | 版本 | 说明 |
|------|------|------|
| PostgreSQL | 15+ | 核心业务库，利用 JSONB 存储动态步骤 |
| Docker | 24+ | 开发/测试环境容器化 |
| Docker Compose | 2.24+ | 本地多服务编排 |


---

## 3. 核心模块详细设计

### 3.1 任务调度与执行引擎（关键修订）

#### 3.1.1 为什么必须解耦

FastAPI 是 ASGI 服务，其设计目标是**快速响应 HTTP 请求**。一个 APP 测试用例的执行时长通常在 **30 秒 ~ 30 分钟** 之间：
- 启动 App → 登录 → 业务操作 → 断言 → 截图 → 生成报告
- 期间涉及大量 I/O 等待（Appium 指令往返、设备响应）

如果在 FastAPI 进程内直接 `await execute_case()`，会**占用一个工作线程/协程长达数分钟**，导致：
- 并发请求处理能力骤降
- 执行异常可能导致整个 API 进程崩溃
- 无法水平扩展执行能力（只能垂直扩容）

#### 3.1.2 第一阶段调度方案：内存队列 + 独立 Worker

**架构原则**：
- FastAPI 只负责"接受请求、校验权限、创建 Execution 记录、写入任务、返回 execution_id"
- Worker 是**独立操作系统进程**，与 FastAPI 无父子关系，通过 PostgreSQL 或内存队列获取任务

**任务队列实现（最小化中间件）**：

由于第一阶段不上 Redis/RabbitMQ，使用 PostgreSQL 表作为轻量队列：

```sql
CREATE TABLE execution_queue (
    id BIGSERIAL PRIMARY KEY,
    execution_id BIGINT NOT NULL REFERENCES executions(id),
    status VARCHAR(20) DEFAULT 'pending',
    claimed_by VARCHAR(100),
    claimed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    retry_count INT DEFAULT 0
);

CREATE INDEX idx_queue_status_created ON execution_queue(status, created_at);
```

**Worker 消费逻辑（伪代码）**：

```python
# worker.py - 独立进程运行
import asyncio
import asyncpg

async def worker_loop(worker_id: str):
    conn = await asyncpg.connect(dsn=DATABASE_URL)
    while True:
        # 使用 SELECT FOR UPDATE SKIP LOCKED 实现分布式安全抢锁
        row = await conn.fetchrow(
            "SELECT id, execution_id FROM execution_queue "
            "WHERE status = 'pending' ORDER BY created_at "
            "FOR UPDATE SKIP LOCKED LIMIT 1"
        )
        if not row:
            await asyncio.sleep(2)
            continue

        queue_id, execution_id = row['id'], row['execution_id']

        # 标记为已认领
        await conn.execute(
            "UPDATE execution_queue SET status = 'claimed', "
            "claimed_by = $1, claimed_at = NOW() WHERE id = $2",
            worker_id, queue_id
        )

        try:
            # 执行测试
            await execute_test(execution_id)
            await conn.execute(
                "UPDATE execution_queue SET status = 'done' WHERE id = $1",
                queue_id
            )
        except Exception:
            await conn.execute(
                "UPDATE execution_queue SET status = 'failed', "
                "retry_count = retry_count + 1 WHERE id = $1",
                queue_id
            )

# 启动方式：python worker.py --worker-id=worker-001
# 可单机启动多个 Worker 进程
```

**FastAPI 侧只负责入队**：

```python
@app.post("/api/executions/cases/{case_id}")
async def run_case(case_id: int, db: AsyncSession = Depends(get_db)):
    # 1. 创建 Execution 记录，状态 = QUEUED
    execution = await create_execution(db, case_id=case_id, type='CASE')

    # 2. 写入队列（立即返回，不等待执行）
    await db.execute(text(
        "INSERT INTO execution_queue (execution_id) VALUES (:eid)"
    ), {"eid": execution.id})
    await db.commit()

    # 3. 立即返回 execution_id，前端轮询或 WebSocket 订阅状态
    return {"execution_id": execution.id, "status": "QUEUED"}
```

#### 3.1.3 执行状态机（修订版）

```
QUEUED
  │
  ▼
RUNNING  ←──  Worker 认领任务后更新
  │
  ├─── 正常完成 ──► PASSED
  │
  ├─── 断言失败 ──► FAILED
  │
  ├─── 执行异常 ──► ERROR
  │
  ├─── 用户停止 ──► STOPPED
  │
  └─── 超时/Agent 失联 ──► ERROR  (由超时监控引擎自动触发)
```

**状态转换规则**：
- `QUEUED → RUNNING`：Worker 开始执行时
- `RUNNING → PASSED/FAILED/ERROR/STOPPED`：Worker 执行完毕时
- **禁止反向转换**：`PASSED → RUNNING` 绝对不允许（重试必须创建新 Execution）
- **超时自动转换**：APScheduler 定时扫描 `RUNNING` 且 `last_heartbeat < NOW() - interval` 的执行，强制改为 `ERROR`

#### 3.1.4 超时与死锁恢复机制

**三层防护**：

1. **执行级超时**
   - 每个 Execution 创建时记录 `started_at` 和 `timeout_seconds`（默认 1800s）
   - APScheduler 每 60 秒扫描超时执行并强制标记为 ERROR，释放设备锁

2. **Agent 心跳超时**
   - Agent 每 30 秒发送 heartbeat
   - 服务器记录 agents.last_heartbeat
   - 失联 Agent 关联的 RUNNING 执行全部标记为 ERROR，设备状态重置为 IDLE

3. **启动自检**
   - FastAPI 启动时，扫描所有 status = 'RUNNING' 的执行
   - 检查对应 Agent 是否在线，不在线则重置为 ERROR（防止服务器重启后状态不一致）


### 3.2 Device Agent 详细设计（重点修订）

#### 3.2.1 交付形态：零依赖可执行文件

**核心决策**：Agent 继续用 Python 开发（利用成熟的 Appium-Python-Client），但**通过 PyInstaller 打包为独立可执行文件**。

**构建产物**：

| 平台 | 产物 | 体积 | 用户操作 |
|------|------|------|----------|
| Windows x64 | `test-agent-v1.0.0-windows.exe` | ~60MB | 下载 → 双击运行 |
| macOS ARM64 | `test-agent-v1.0.0-macos-arm64` | ~55MB | 下载 → `./test-agent` |
| macOS x64 | `test-agent-v1.0.0-macos-x64` | ~55MB | 下载 → `./test-agent` |
| Linux x64 | `test-agent-v1.0.0-linux-x64` | ~58MB | 下载 → `./test-agent` |
| Docker | `test-platform/agent:v1.0.0` | ~200MB | `docker run ...` |

**打包命令**：
```bash
# Windows
pyinstaller --onefile --name test-agent-windows-x64 \
  --hidden-import=appium \
  --hidden-import=websockets \
  --add-data "config.yaml;." \
  agent/main.py

# macOS (针对 Apple Silicon)
pyinstaller --onefile --name test-agent-macos-arm64 \
  --target-arch arm64 \
  agent/main.py
```

#### 3.2.2 Agent 配置与认证

**配置文件 `config.yaml`**（与可执行文件同目录）：

```yaml
# 服务器连接
server: "wss://test.example.com/ws/agent"
agent_key: "sk-agent-xxxxxxxxxxxxxxxx"      # 预共享密钥，从平台"设备管理"页面获取
agent_id: "agent-001"                        # 可选，不填则服务器分配

# 可选配置
heartbeat_interval: 30                       # 心跳间隔（秒）
log_level: "INFO"
appium_host: "127.0.0.1"
appium_port: 4723
```

**注册握手流程**：

```
Agent                    FastAPI Server
  │                           │
  │ ── WebSocket 连接 ──────► │
  │                           │
  │ ── register ────────────► │
  │   {                       │
  │     "type": "register",   │
  │     "agent_key": "sk-..", │
  │     "agent_id": "agent-001",
  │     "hostname": "test-pc-01",
  │     "platform": "windows",│
  │     "version": "1.0.0"    │
  │   }                       │
  │                           │
  │ ◄── registered ────────── │
  │   {                       │
  │     "type": "registered", │
  │     "agent_id": "agent-001",
  │     "status": "ok"        │
  │   }                       │
  │                           │
  │ ◄──── heartbeat ───────── │  (每 30 秒)
  │ ──── heartbeat ─────────► │
```

**认证失败处理**：
- 如果 `agent_key` 无效 → 服务器返回 `{"type": "error", "code": "AUTH_FAILED"}` 并断开连接
- Agent 收到认证失败后，**不再自动重连**（防止暴力破解），写入错误日志并退出
- 用户需检查 config.yaml 中的 agent_key 是否正确

#### 3.2.3 Agent 内部架构

```
Device Agent (独立进程)
│
├── main.py
│   ├── ConfigLoader (加载 config.yaml)
│   ├── SignalHandler (Ctrl+C 优雅退出)
│   └── 版本检查 (连接时上报 version，服务器可提示更新)
│
├── connection/
│   ├── ws_client.py          # WebSocket 连接管理 + 自动重连
│   ├── auth.py               # PSK 认证 + 注册握手
│   └── heartbeat.py          # 定时心跳发送
│
├── device/
│   ├── android_manager.py    # ADB 设备发现、状态监控
│   ├── ios_manager.py        # xcrun simctl 设备发现
│   └── device_registry.py    # 本地设备列表维护
│
├── appium/
│   ├── server_manager.py     # 启动/停止 Appium Server
│   ├── session_manager.py    # driver 生命周期管理 (上下文管理器)
│   └── cleanup.py            # 异常退出时的 Session 清理
│
├── executor/
│   ├── command_handler.py    # 接收服务器命令 (start_test, stop_test)
│   ├── test_runner.py        # 调用 Appium 执行步骤
│   ├── screenshot.py         # 截图保存与上传
│   └── log_forwarder.py      # 实时日志回传服务器
│
└── utils/
    ├── logger.py             # 本地日志轮转
    └── uploader.py           # 文件上传 (截图、日志、报告)
```

**Appium Session 生命周期管理（关键）**：

```python
class AppiumSession:
    def __init__(self, device, caps):
        self.device = device
        self.caps = caps
        self.driver = None

    async def __aenter__(self):
        self.driver = webdriver.Remote(
            command_executor=f"http://{APPIUM_HOST}:{APPIUM_PORT}",
            desired_capabilities=self.caps
        )
        # 设置命令超时，防止 Session 永久残留
        self.driver.command_executor.set_timeout(300)
        return self.driver

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass  # 即使 quit 失败也要继续清理
        # 兜底：通过 Appium API 强制删除 Session
        await force_kill_session(self.driver.session_id)
```

**Agent 启动前清理**：
- 检查并杀掉残留的 Appium 进程
- `adb kill-server && adb start-server`（Android）
- 清理 `/tmp` 下的旧截图和日志文件

#### 3.2.4 服务器端 Agent 管理

**Agent 表结构修订**：

```sql
CREATE TABLE agents (
    id BIGSERIAL PRIMARY KEY,
    agent_key VARCHAR(255) NOT NULL UNIQUE,     -- 预共享密钥
    agent_id VARCHAR(100) NOT NULL UNIQUE,
    hostname VARCHAR(255),
    platform VARCHAR(50),                        -- windows / macos / linux
    ip INET,
    status VARCHAR(20) DEFAULT 'offline',        -- offline / online / busy
    version VARCHAR(50),
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

**Agent 版本管理**：
- 连接时 Agent 上报 `version`
- 服务器维护 `min_agent_version` 配置
- 如果 Agent 版本过低，返回 `{"type": "upgrade_required", "download_url": "..."}`
- Agent 收到后弹窗/日志提示用户下载新版本


### 3.3 数据库设计细化

#### 3.3.1 核心表结构（含修订）

**users**（不变）
```sql
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,         -- Argon2id
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

**projects**（不变）
```sql
CREATE TABLE projects (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    owner_id BIGINT NOT NULL REFERENCES users(id),
    visibility VARCHAR(20) DEFAULT 'private',    -- private / public
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);
```

**project_members**（新增，预留多人协作）
```sql
CREATE TABLE project_members (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    role VARCHAR(20) DEFAULT 'member',           -- owner / admin / member / viewer
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(project_id, user_id)
);
```

**test_modules**（不变）
```sql
CREATE TABLE test_modules (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    parent_id BIGINT REFERENCES test_modules(id), -- 支持树形，第一阶段建议最多 2 级
    name VARCHAR(255) NOT NULL,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);
```

**test_elements**（不变）
```sql
CREATE TABLE test_elements (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    name VARCHAR(255) NOT NULL,
    page_name VARCHAR(255),
    platform VARCHAR(20),                        -- android / ios / both
    locator_type VARCHAR(50),                    -- id / resource_id / xpath / accessibility_id / class_name / uiautomator / predicate / coordinate / custom
    locator_value TEXT NOT NULL,
    description TEXT,
    created_by BIGINT REFERENCES users(id),
    updated_by BIGINT REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);
```

**test_cases**（核心，利用 JSONB）
```sql
CREATE TABLE test_cases (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    module_id BIGINT REFERENCES test_modules(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(20) DEFAULT 'draft',          -- draft / active / disabled
    steps JSONB NOT NULL DEFAULT '[]',
    assertions JSONB NOT NULL DEFAULT '[]',
    variables JSONB DEFAULT '{}',                -- 用例级变量覆盖
    created_by BIGINT REFERENCES users(id),
    updated_by BIGINT REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);
```

**test_suites & test_suite_cases**（不变）
```sql
CREATE TABLE test_suites (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(20) DEFAULT 'active',
    created_by BIGINT REFERENCES users(id),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);

CREATE TABLE test_suite_cases (
    id BIGSERIAL PRIMARY KEY,
    suite_id BIGINT NOT NULL REFERENCES test_suites(id),
    case_id BIGINT NOT NULL REFERENCES test_cases(id),
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(suite_id, case_id)
);
CREATE INDEX idx_suite_cases_order ON test_suite_cases(suite_id, sort_order);
```

**devices**（修订，增加乐观锁字段）
```sql
CREATE TABLE devices (
    id BIGSERIAL PRIMARY KEY,
    agent_id BIGINT NOT NULL REFERENCES agents(id),
    name VARCHAR(255) NOT NULL,
    platform VARCHAR(20) NOT NULL,               -- android / ios
    platform_version VARCHAR(50),
    udid VARCHAR(255) NOT NULL,
    device_type VARCHAR(20) DEFAULT 'emulator',  -- real / emulator / simulator
    status VARCHAR(20) DEFAULT 'idle',           -- idle / busy / offline / error
    locked_by_execution BIGINT REFERENCES executions(id), -- 乐观锁核心字段
    capabilities JSONB DEFAULT '{}',
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_devices_status ON devices(status);
CREATE INDEX idx_devices_agent ON devices(agent_id);
```

**executions**（修订，增加超时与重试）
```sql
CREATE TABLE executions (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    type VARCHAR(20) NOT NULL,                   -- case / suite / batch
    suite_id BIGINT REFERENCES test_suites(id),
    case_id BIGINT REFERENCES test_cases(id),
    device_id BIGINT REFERENCES devices(id),
    status VARCHAR(20) DEFAULT 'queued',         -- queued / running / passed / failed / error / stopped / cancelled
    parameters JSONB DEFAULT '{}',               -- 执行参数快照
    timeout_seconds INT DEFAULT 1800,            -- 执行超时时间（秒）
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    duration INT,                                -- 实际耗时（毫秒）
    created_by BIGINT REFERENCES users(id),
    retry_of BIGINT REFERENCES executions(id),   -- 重试关联
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_executions_status ON executions(status);
CREATE INDEX idx_executions_project ON executions(project_id);
CREATE INDEX idx_executions_retry ON executions(retry_of);
```

**execution_cases**（关键修订：增加快照字段）
```sql
CREATE TABLE execution_cases (
    id BIGSERIAL PRIMARY KEY,
    execution_id BIGINT NOT NULL REFERENCES executions(id),
    case_id BIGINT NOT NULL,
    case_name VARCHAR(255) NOT NULL,             -- 执行时快照
    module_name VARCHAR(255),                    -- 同上
    status VARCHAR(20) DEFAULT 'pending',
    steps_snapshot JSONB NOT NULL,               -- 执行时的完整步骤副本
    assertions_snapshot JSONB NOT NULL,          -- 执行时的完整断言副本
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    duration INT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_exec_cases_execution ON execution_cases(execution_id);
```

**execution_steps**（不变）
```sql
CREATE TABLE execution_steps (
    id BIGSERIAL PRIMARY KEY,
    execution_case_id BIGINT NOT NULL REFERENCES execution_cases(id),
    step_order INT NOT NULL,
    action VARCHAR(50) NOT NULL,
    parameters JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'pending',
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    duration INT,
    actual_value TEXT,                           -- 实际获取的值
    error_message TEXT,
    screenshot_path TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**execution_assertions**（不变）
```sql
CREATE TABLE execution_assertions (
    id BIGSERIAL PRIMARY KEY,
    execution_step_id BIGINT NOT NULL REFERENCES execution_steps(id),
    assertion_type VARCHAR(50) NOT NULL,
    expected_value TEXT,
    actual_value TEXT,
    status VARCHAR(20) DEFAULT 'pending',        -- pass / fail
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**execution_logs**（关键：先入库再推送的存储基础）
```sql
CREATE TABLE execution_logs (
    id BIGSERIAL PRIMARY KEY,
    execution_id BIGINT NOT NULL REFERENCES executions(id),
    level VARCHAR(20) NOT NULL,                  -- DEBUG / INFO / WARN / ERROR
    message TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    source VARCHAR(50) DEFAULT 'worker'          -- worker / agent / appium
);

CREATE INDEX idx_logs_execution ON execution_logs(execution_id);
CREATE INDEX idx_logs_timestamp ON execution_logs(timestamp);
-- 未来数据量大时，可按 created_at 做表分区
```

**reports**（不变）
```sql
CREATE TABLE reports (
    id BIGSERIAL PRIMARY KEY,
    execution_id BIGINT NOT NULL REFERENCES executions(id),
    total INT DEFAULT 0,
    passed INT DEFAULT 0,
    failed INT DEFAULT 0,
    error_count INT DEFAULT 0,
    skipped INT DEFAULT 0,
    success_rate DECIMAL(5,2) DEFAULT 0.00,
    duration INT,
    report_path TEXT,                            -- /data/reports/2026/08/20/exec_10001/
    created_at TIMESTAMP DEFAULT NOW()
);
```

**execution_queue**（新增，轻量任务队列）
```sql
CREATE TABLE execution_queue (
    id BIGSERIAL PRIMARY KEY,
    execution_id BIGINT NOT NULL REFERENCES executions(id),
    status VARCHAR(20) DEFAULT 'pending',        -- pending / claimed / done / failed
    claimed_by VARCHAR(100),
    claimed_at TIMESTAMP,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_queue_pending ON execution_queue(status, created_at) WHERE status = 'pending';
```

#### 3.3.2 步骤与断言 JSONB 结构规范

**steps 示例**：
```json
[
  {
    "order": 1,
    "action": "launch_app",
    "params": {
      "package": "com.demo.app",
      "activity": "com.demo.app.MainActivity",
      "no_reset": false
    },
    "description": "启动应用"
  },
  {
    "order": 2,
    "action": "click",
    "element_id": 1001,
    "params": {
      "wait_timeout": 10
    },
    "description": "点击登录按钮"
  },
  {
    "order": 3,
    "action": "input",
    "element_id": 1002,
    "params": {
      "value": "${username}",
      "clear_first": true
    },
    "description": "输入用户名"
  },
  {
    "order": 4,
    "action": "sleep",
    "params": {
      "duration": 2
    }
  }
]
```

**assertions 示例**：
```json
[
  {
    "order": 1,
    "type": "element_exists",
    "element_id": 1003,
    "params": {
      "expected": true,
      "wait_timeout": 10
    },
    "description": "首页元素存在"
  },
  {
    "order": 2,
    "type": "text_equals",
    "element_id": 1004,
    "params": {
      "expected": "登录成功",
      "trim": true
    }
  },
  {
    "order": 3,
    "type": "attribute_contains",
    "element_id": 1005,
    "params": {
      "attribute": "content-desc",
      "expected": "个人中心"
    }
  }
]
```

**变量渲染规则**：
- 支持 `${var_name}` 语法
- 变量优先级（高到低）：执行参数 > 套件变量 > 用例变量 > 项目变量 > 全局变量
- Worker 执行前，统一渲染 steps 和 assertions 中的变量占位符


### 3.4 REST API 设计细化

#### 3.4.1 认证相关

```
POST   /api/auth/register          # 注册
POST   /api/auth/login             # 登录，返回 JWT
GET    /api/auth/me                # 获取当前用户信息
POST   /api/auth/logout            # 登出（前端清除 Token）
POST   /api/auth/refresh           # 刷新 JWT（可选，延长登录态）
```

**JWT 规范**：
- 放在 `Authorization: Bearer <token>` Header
- 有效期：Access Token 2 小时，Refresh Token 7 天
- Payload 包含：`user_id`, `username`, `exp`

#### 3.4.2 项目管理

```
GET    /api/projects?page=1&page_size=20&visibility=all
POST   /api/projects
GET    /api/projects/{id}
PUT    /api/projects/{id}
DELETE /api/projects/{id}
GET    /api/projects/{id}/members          # 预留：项目成员列表
POST   /api/projects/{id}/members          # 预留：添加成员
```

**权限校验规则**：
- `GET /api/projects`：返回 `owner_id = current_user.id` 的私有项目 + 所有公开项目
- 其他操作：仅 `owner_id = current_user.id` 或 project_members 中有 ADMIN 角色可操作
- 公开项目：`viewer` 角色可查看，但不可编辑/删除

#### 3.4.3 模块管理

```
GET    /api/projects/{project_id}/modules?parent_id=0
POST   /api/projects/{project_id}/modules
PUT    /api/modules/{id}
DELETE /api/modules/{id}
```

#### 3.4.4 元素管理

```
GET    /api/projects/{project_id}/elements?page=1&page_size=20&keyword=&platform=
POST   /api/projects/{project_id}/elements
GET    /api/elements/{id}
PUT    /api/elements/{id}
DELETE /api/elements/{id}
GET    /api/elements/{id}/usage            # 新增：查看被哪些用例引用
```

**元素权限**：所有人可查看/编辑/使用，但只能操作自己可访问项目下的元素。

#### 3.4.5 用例管理

```
GET    /api/projects/{project_id}/cases?page=1&page_size=20&module_id=&keyword=&status=
POST   /api/projects/{project_id}/cases
GET    /api/cases/{id}
PUT    /api/cases/{id}
DELETE /api/cases/{id}
POST   /api/cases/{id}/clone             # 新增：克隆用例
```

**删除逻辑**：`UPDATE test_cases SET deleted_at = NOW() WHERE id = ?`

#### 3.4.6 套件管理

```
GET    /api/projects/{project_id}/suites
POST   /api/projects/{project_id}/suites
GET    /api/suites/{id}
PUT    /api/suites/{id}
DELETE /api/suites/{id}

GET    /api/suites/{id}/cases
POST   /api/suites/{id}/cases            # body: {case_id}
PUT    /api/suites/{id}/cases/order      # body: [{case_id, sort_order}]
DELETE /api/suites/{id}/cases/{case_id}
```

#### 3.4.7 执行管理（关键修订）

```
POST   /api/executions/cases/{case_id}         # 单用例执行
POST   /api/executions/suites/{suite_id}       # 单套件执行
POST   /api/executions/suites/batch            # 多套件批量执行

GET    /api/executions?page=1&page_size=20&project_id=&status=
GET    /api/executions/{id}
GET    /api/executions/{id}/logs?after_timestamp=2026-08-20T10:00:00  # 新增：拉取历史日志
POST   /api/executions/{id}/stop               # 请求停止（发送信号给 Worker）
POST   /api/executions/{id}/retry              # 重试（创建新 Execution，复制 parameters）
```

**执行创建响应**：
```json
{
  "execution_id": 10001,
  "status": "QUEUED",
  "estimated_start": "2026-08-20T10:05:00",
  "ws_url": "wss://test.example.com/ws/executions/10001"
}
```

**重试逻辑**：
1. 查询原 Execution 的 `parameters` 和 `retry_of` 链
2. 创建新 Execution，复制 `parameters`
3. 新 Execution 的 `retry_of = 原 Execution.id`
4. 新 Execution 入队，返回新 `execution_id`

#### 3.4.8 报告管理

```
GET    /api/reports?page=1&page_size=20&project_id=&execution_id=
GET    /api/reports/{id}
GET    /api/reports/{id}/cases               # 用例结果列表
GET    /api/reports/{id}/logs                # 执行日志（从 execution_logs 聚合）
GET    /api/reports/{id}/files/{filename}    # 安全代理读取报告附件
```

**文件代理安全逻辑**：
```python
@app.get("/api/reports/{id}/files/{filename}")
async def get_report_file(id: int, filename: str, user: User = Depends(get_current_user)):
    report = await get_report(id)
    # 1. 权限校验：用户是否有权访问该 report 关联的 project
    await check_project_permission(user, report.project_id)

    # 2. 路径安全校验：禁止 ../ 等路径遍历
    safe_filename = secure_filename(filename)
    file_path = Path(report.report_path) / safe_filename

    # 3. 校验 file_path 确实在 report.report_path 目录下
    if not is_safe_path(report.report_path, file_path):
        raise HTTPException(403, "Invalid file path")

    # 4. 返回文件
    return FileResponse(file_path)
```

#### 3.4.9 设备与 Agent 管理（新增）

```
GET    /api/agents                          # Agent 列表（管理员/项目维度）
POST   /api/agents                          # 注册新 Agent（生成 agent_key）
DELETE /api/agents/{id}                     # 注销 Agent
GET    /api/agents/{id}/devices             # Agent 下的设备列表

GET    /api/devices?page=1&page_size=20&platform=&status=
GET    /api/devices/{id}
POST   /api/devices/{id}/release            # 强制释放设备锁（管理员应急）
```

**Agent 注册**：
```json
POST /api/agents
Request:
{
  "hostname": "test-mac-01",
  "platform": "macos"
}
Response:
{
  "agent_id": "agent-001",
  "agent_key": "sk-agent-xxxxxxxxxxxxxxxx",
  "download_url": "https://.../test-agent-macos-arm64"
}
```

### 3.5 WebSocket 设计细化

#### 3.5.1 执行日志通道 `/ws/executions/{execution_id}`

**连接流程**：
1. 前端建立 WebSocket 连接，携带 `?token=<jwt>`
2. 服务器校验 JWT，确认用户有权访问该 execution 关联的 project
3. 校验通过则加入该 execution 的广播组
4. 前端发送 `{"type": "subscribe", "execution_id": 10001}`

**消息格式**：

服务器 → 前端：
```json
// 状态变更
{
  "type": "status",
  "execution_id": 10001,
  "status": "RUNNING",
  "timestamp": "2026-08-20T10:00:00Z"
}

// 实时日志
{
  "type": "log",
  "execution_id": 10001,
  "level": "INFO",
  "message": "点击登录按钮",
  "step_order": 2,
  "timestamp": "2026-08-20T10:00:03Z"
}

// 步骤结果
{
  "type": "step_result",
  "execution_id": 10001,
  "case_id": 500,
  "step_order": 2,
  "status": "passed",
  "case_status": "running",
  "duration": 1500,
  "screenshot_url": "/api/reports/.../step_002.png",
  "timestamp": "2026-08-20T10:00:05Z"
}

// 执行完成
{
  "type": "completed",
  "execution_id": 10001,
  "status": "PASSED",
  "report_id": 2001,
  "timestamp": "2026-08-20T10:05:00Z"
}
```

`step_result.status` 仅表示步骤状态；FastAPI 必须独立广播 `case_status`。收到 Agent `execution_result` 时，FastAPI 在广播 `completed` 前立即将仍为 `pending/running` 的用例收敛为对应终态，Worker 后续继续负责报告汇总和设备释放。

**前端重连策略**：
```javascript
// 使用 VueUse 的 useWebSocket 或自研封装
const { status, data, send, open, close } = useWebSocket(wsUrl, {
  autoReconnect: {
    retries: 10,
    delay: 1000,
    onFailed() { alert('WebSocket 连接失败') }
  },
  heartbeat: {
    message: JSON.stringify({type: 'ping'}),
    interval: 30000,
    pongTimeout: 5000
  },
  onConnected() {
    // 重连后先拉取历史日志，避免遗漏
    fetchHistoricalLogs(executionId, lastReceivedTimestamp)
  }
})
```

#### 3.5.2 Agent 通道 `/ws/agent`

**Agent → 服务器**：
```json
{"type": "register", "agent_key": "sk-...", "agent_id": "...", "hostname": "...", "version": "1.0.0"}
{"type": "heartbeat"}
{"type": "device_list", "devices": [{"udid": "...", "name": "...", "status": "idle"}]}
{"type": "log", "execution_id": 10001, "level": "INFO", "message": "..."}
{"type": "screenshot", "execution_id": 10001, "step_order": 2, "data": "base64..."}
{"type": "execution_result", "execution_id": 10001, "status": "PASSED", "report_path": "..."}
```

**服务器 → Agent**：
```json
{"type": "registered", "agent_id": "agent-001", "status": "ok"}
{"type": "start_test", "execution_id": 10001, "parameters": {...}}
{"type": "stop_test", "execution_id": 10001}
{"type": "upgrade_required", "download_url": "https://..."}
```


### 3.6 执行引擎详细设计

#### 3.6.1 Action Registry

```python
# app/executor/actions/__init__.py
from typing import Dict, Type
from .base import BaseAction

ACTION_REGISTRY: Dict[str, Type[BaseAction]] = {}

def register_action(name: str):
    def decorator(cls: Type[BaseAction]):
        ACTION_REGISTRY[name] = cls
        return cls
    return decorator

# app/executor/actions/base.py
class BaseAction:
    async def execute(self, driver, context, params: dict) -> dict:
        # Returns: {"status": "passed", "actual_value": ..., "screenshot": ...}
        raise NotImplementedError

# app/executor/actions/click.py
@register_action("click")
class ClickAction(BaseAction):
    async def execute(self, driver, context, params: dict) -> dict:
        element = await context.find_element(params.get("element_id"))
        element.click()
        return {"status": "passed"}
```

**支持的动作列表（V1）**：

| 动作 | 说明 | 关键参数 |
|------|------|----------|
| `launch_app` | 启动 APP | `package`, `activity`, `no_reset` |
| `close_app` | 关闭 APP | `package` |
| `click` | 点击元素 | `element_id`, `wait_timeout` |
| `input` | 输入文本 | `element_id`, `value`, `clear_first` |
| `clear` | 清空输入框 | `element_id` |
| `swipe` | 滑动 | `direction` (up/down/left/right), `duration` |
| `scroll` | 滚动到元素 | `element_id` |
| `back` | 返回键 | - |
| `sleep` | 等待 | `duration` (秒) |
| `screenshot` | 截图 | `filename` (可选) |
| `get_text` | 获取文本 | `element_id`, `variable_name` (存入变量) |
| `get_attribute` | 获取属性 | `element_id`, `attribute`, `variable_name` |
| `tap_coordinate` | 坐标点击 | `x`, `y` |

#### 3.6.2 Assertion Registry

```python
# app/executor/assertions/__init__.py
ASSERTION_REGISTRY: Dict[str, Type[BaseAssertion]] = {}

# app/executor/assertions/text_equals.py
@register_assertion("text_equals")
class TextEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = await context.find_element(params.get("element_id"))
        actual = element.text.strip() if params.get("trim") else element.text
        expected = params.get("expected")
        passed = actual == expected
        return {
            "status": "passed" if passed else "failed",
            "expected": expected,
            "actual": actual
        }
```

**支持的断言类型（V1）**：

| 断言类型 | 说明 |
|----------|------|
| `element_exists` | 元素存在/不存在 |
| `text_equals` | 文本完全匹配 |
| `text_contains` | 文本包含 |
| `text_not_contains` | 文本不包含 |
| `attribute_equals` | 属性值完全匹配 |
| `attribute_contains` | 属性值包含 |
| `value_equals` | 输入框值匹配 |
| `regex_match` | 正则匹配 |

#### 3.6.3 执行上下文 (Context)

```python
class ExecutionContext:
    def __init__(self, execution_id: int, parameters: dict, db_session):
        self.execution_id = execution_id
        self.parameters = parameters
        self.variables = parameters.get("variables", {})
        self.db = db_session
        self.screenshots_dir = f"/data/reports/{date.today()}/execution_{execution_id}/screenshots"

    async def find_element(self, element_id: int):
        # 从 steps_snapshot 中解析定位信息，而非实时查询 test_elements
        element_data = self.element_cache[element_id]
        locator_type = element_data["locator_type"]
        locator_value = self.render_variables(element_data["locator_value"])
        return self.driver.find_element(
            getattr(AppiumBy, locator_type.upper()), 
            locator_value
        )

    def render_variables(self, text: str) -> str:
        # 渲染 ${var_name} 为实际值
        for key, value in self.variables.items():
            text = text.replace(f"${{{key}}}", str(value))
        return text

    async def save_screenshot(self, step_order: int) -> str:
        path = f"{self.screenshots_dir}/step_{step_order:03d}.png"
        self.driver.save_screenshot(path)
        return path
```

#### 3.6.4 主执行流程

```python
class TestExecutor:
    async def execute_case(self, case_result: ExecutionCase, context: ExecutionContext):
        # 1. 加载快照（不查实时 test_cases）
        steps = case_result.steps_snapshot
        assertions = case_result.assertions_snapshot

        for step_data in steps:
            step_record = await create_execution_step(case_result.id, step_data)

            try:
                # 2. 执行动作
                action_cls = ACTION_REGISTRY[step_data["action"]]
                action = action_cls()
                result = await action.execute(context.driver, context, step_data.get("params", {}))

                # 3. 截图（每个步骤后自动截图，失败时额外截图）
                screenshot_path = await context.save_screenshot(step_data["order"])

                # 4. 更新步骤结果
                await update_step_status(step_record.id, "passed", result, screenshot_path)

                # 5. 实时推送日志（先入库，再 WebSocket）
                await log_and_broadcast(
                    execution_id=context.execution_id,
                    level="INFO",
                    message=f"Step {step_data['order']}: {step_data['action']} passed",
                    step_order=step_data["order"]
                )

            except Exception as e:
                screenshot_path = await context.save_screenshot(step_data["order"])
                await update_step_status(step_record.id, "failed", error=str(e), screenshot=screenshot_path)
                await log_and_broadcast(context.execution_id, "ERROR", str(e), step_data["order"])
                raise StepExecutionError(e)

        # 6. 执行断言
        for assertion_data in assertions:
            assertion_record = await create_execution_assertion(step_record.id, assertion_data)
            try:
                assertion_cls = ASSERTION_REGISTRY[assertion_data["type"]]
                result = await assertion_cls().verify(context.driver, context, assertion_data.get("params", {}))
                await update_assertion_status(assertion_record.id, result)
            except Exception as e:
                await update_assertion_status(assertion_record.id, {"status": "failed", "error": str(e)})
```

### 3.7 报告生成设计

**报告目录结构**：
```
/data/reports/
  2026/
    08/
      20/
        execution_10001/
          ├── report.html          # 前端可直接打开的 HTML 报告
          ├── report.json          # 结构化数据，供 API 消费
          ├── screenshots/
          │     ├── step_001.png
          │     ├── step_002.png
          │     └── step_003_fail.png
          └── logs/
                └── execution.log
```

**HTML 报告生成方式**：
- 使用 Jinja2 模板引擎，在 Worker 进程内生成静态 HTML
- 模板包含：执行概览、统计卡片、用例列表、步骤详情、断言结果、截图画廊、日志查看器
- 前端提供"下载 HTML 报告"按钮，直接下载完整包

**报告数据聚合**：
```python
async def generate_report(execution_id: int) -> Report:
    cases = await get_execution_cases(execution_id)
    total = len(cases)
    passed = sum(1 for c in cases if c.status == "passed")
    failed = sum(1 for c in cases if c.status == "failed")
    error_count = sum(1 for c in cases if c.status == "error")

    report = Report(
        execution_id=execution_id,
        total=total,
        passed=passed,
        failed=failed,
        error_count=error_count,
        success_rate=round(passed / total * 100, 2) if total > 0 else 0,
        duration=sum(c.duration for c in cases),
        report_path=f"/data/reports/.../execution_{execution_id}"
    )
    return report
```


---

## 4. 安全设计

### 4.1 认证与授权

| 层级 | 措施 |
|------|------|
| 密码存储 | Argon2id 哈希，绝对禁止明文 |
| JWT | RS256 非对称签名，私钥在服务端，公钥可分发 |
| Token 传输 | `Authorization: Bearer <token>` Header |
| Token 刷新 | Access Token 2h + Refresh Token 7d，刷新端点校验 Refresh Token 黑名单 |
| WebSocket 鉴权 | 连接时通过 Query Param `?token=` 传递 JWT，服务端 `on_connect` 时校验 |
| Agent 认证 | PSK (Pre-Shared Key)，Agent 配置文件预置，注册时校验 |

### 4.2 权限矩阵

| 资源 | 创建者 | 项目成员(ADMIN) | 项目成员(MEMBER) | 其他登录用户(公开项目) | 访客 |
|------|--------|-----------------|------------------|------------------------|------|
| 查看项目 | ✓ | ✓ | ✓ | ✓ | ✗ |
| 编辑项目 | ✓ | ✓ | ✗ | ✗ | ✗ |
| 删除项目 | ✓ | ✗ | ✗ | ✗ | ✗ |
| 查看用例/套件 | ✓ | ✓ | ✓ | ✓ | ✗ |
| 编辑用例/套件 | ✓ | ✓ | ✓ | ✗ | ✗ |
| 删除用例/套件 | ✓ | ✓ | ✓ | ✗ | ✗ |
| 执行测试 | ✓ | ✓ | ✓ | ✗ | ✗ |
| 查看报告 | ✓ | ✓ | ✓ | ✓ | ✗ |
| 编辑元素 | ✓ | ✓ | ✓ | ✓ | ✗ |
| 管理设备/Agent | ✓ | ✓ | ✗ | ✗ | ✗ |

### 4.3 输入安全

- **SQL 注入**：全部使用 SQLAlchemy ORM / 参数化查询，禁止字符串拼接 SQL
- **XSS**：前端 Vue 默认转义；后端返回给前端的文本数据做 HTML 转义
- **CSRF**：使用 JWT 而非 Cookie Session，天然免疫 CSRF
- **文件上传**：限制扩展名（`.apk`, `.ipa`, `.png`, `.jpg`, `.log`, `.txt`），限制大小（APK 最大 500MB，截图最大 10MB），使用 UUID 重命名，禁止用户输入决定存储路径
- **路径遍历**：报告文件访问通过 API 代理，使用 `secure_filename` + `is_safe_path` 双重校验

### 4.4 网络安全

- **CORS**：严格限制允许的 Origin，生产环境不允许 `*`
- **HTTPS/WSS**：生产环境强制 TLS，禁止明文 HTTP/WS
- **Rate Limiting**：FastAPI 层使用 `slowapi` 或自研中间件，限制关键接口（登录、注册、执行创建）频率

---

## 5. 部署方案

### 5.1 开发环境

```yaml
# docker-compose.dev.yml
version: '3.8'
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: test_platform
      POSTGRES_USER: dev
      POSTGRES_PASSWORD: dev123
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data

  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://dev:dev123@postgres:5432/test_platform
      JWT_SECRET: dev-secret-key
    volumes:
      - ./backend:/app
      - ./data/reports:/data/reports
    command: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

  worker:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://dev:dev123@postgres:5432/test_platform
    volumes:
      - ./backend:/app
      - ./data/reports:/data/reports
    command: python worker.py --worker-id=dev-worker-001
    # 开发环境 Worker 与 Backend 共享代码，但独立进程

  frontend:
    build: ./frontend
    ports:
      - "5173:5173"
    volumes:
      - ./frontend:/app
    command: npm run dev

volumes:
  pg_data:
```

**开发环境启动**：
```bash
docker-compose -f docker-compose.dev.yml up -d
# 本地启动 Agent（开发模式，不打包）
cd agent && python main.py --config config.dev.yaml
```

### 5.2 测试/生产环境

```
用户浏览器
    │
    ▼
Nginx (HTTPS 入口, 静态文件服务, 反向代理)
    │
    ├──► /api/*  → FastAPI (Uvicorn, 多 worker 进程)
    │
    └──► / → Vue 3 构建产物 (dist/)

FastAPI
    │
    ├──► PostgreSQL (主库)
    │
    └──► 本地文件系统 /data/reports (报告存储)

Test Worker (独立服务器或同一台服务器的独立进程)
    │
    ├──► PostgreSQL (读取任务队列)
    │
    ├──► FastAPI WebSocket (推送日志)
    │
    └──► Device Agent (通过 WebSocket 下发任务)

Device Agent (用户电脑 / 测试机)
    │
    ├──► Appium Server
    │
    └──► ADB / xcrun simctl
```

**生产环境部署清单**：

| 组件 | 部署方式 | 说明 |
|------|----------|------|
| PostgreSQL | Docker 或托管 RDS | 建议启用自动备份 |
| FastAPI | systemd / Docker | 使用 Gunicorn + Uvicorn Worker，4-8 进程 |
| Test Worker | systemd / Docker | 可单机多实例，也可多机部署 |
| Vue 静态文件 | Nginx / FastAPI 托管 | Nginx 性能更好，也可由 FastAPI 直接 serve |
| 报告文件 | 本地 SSD / NAS | 预留足够空间，配置定期清理 |
| Nginx | 系统包管理器安装 | 配置 HTTPS，HTTP/2，Gzip |

**Nginx 配置示例**：
```nginx
server {
    listen 443 ssl http2;
    server_name test.example.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    # 前端静态文件
    location / {
        root /var/www/test-platform/dist;
        try_files $uri $uri/ /index.html;
        expires 1d;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }

    # WebSocket 代理
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
    }
}
```

### 5.3 Agent 分发

**发布流程**：
1. Git Tag 触发 CI (GitHub Actions / GitLab CI)
2. 各平台并行构建：
   - Windows: `pyinstaller ...` → `test-agent-windows-x64.exe`
   - macOS ARM: `pyinstaller ...` → `test-agent-macos-arm64`
   - macOS x64: `pyinstaller ...` → `test-agent-macos-x64`
   - Linux: `pyinstaller ...` → `test-agent-linux-x64`
   - Docker: `docker build ...` → `registry.test.com/agent:v1.0.0`
3. 上传至 Release 页面 / 企业内部网盘 / CDN
4. 服务器更新 `min_agent_version`，提示旧版本 Agent 升级


---

## 6. 开发里程碑与排期建议

### Phase 1: 基础框架 (2 周)
- [ ] Vue3 + Vite + Element Plus 项目脚手架
- [ ] FastAPI + SQLAlchemy 2.0 + Alembic 项目脚手架
- [ ] PostgreSQL Docker 环境
- [ ] JWT 认证体系（注册、登录、Token 刷新）
- [ ] 用户管理页面

### Phase 2: 项目与元素 (2 周)
- [ ] 项目管理（CRUD、PUBLIC/PRIVATE）
- [ ] 模块管理（树形结构，最多 2 级）
- [ ] 页面元素管理（多种定位方式）
- [ ] 元素引用查看（被哪些用例使用）

### Phase 3: 用例管理 (3 周)
- [ ] 用例列表（模块筛选、搜索、分页）
- [ ] 用例编辑器（步骤编辑器、断言编辑器）
- [ ] 步骤 Action 前端组件（下拉选择 + 参数表单）
- [ ] 断言前端组件
- [ ] 用例克隆功能
- [ ] 虚拟删除（deleted_at）

### Phase 4: 套件管理 (1.5 周)
- [ ] 套件 CRUD
- [ ] 用例搜索与添加
- [ ] 拖拽排序（VueDraggable）
- [ ] 套件执行参数配置

### Phase 5: 执行引擎核心 (3 周)
- [ ] 任务队列表 + Worker 进程框架
- [ ] Action Registry + 核心动作实现（click, input, launch_app, sleep, screenshot 等）
- [ ] Assertion Registry + 核心断言实现
- [ ] 执行状态机 + 超时监控（APScheduler）
- [ ] 设备乐观锁（数据库层）
- [ ] 单用例执行链路打通

### Phase 6: Agent 与设备 (3 周)
- [ ] Agent WebSocket 连接管理 + PSK 认证
- [ ] Agent 心跳与设备发现（ADB / simctl）
- [ ] Agent 打包脚本（PyInstaller，Windows/macOS/Linux）
- [ ] Appium Session 管理（启动、连接、清理、超时）
- [ ] Agent 接收任务 → 执行 → 回传结果完整链路
- [ ] 设备管理页面（在线状态、占用状态）

### Phase 7: 实时与日志 (2 周)
- [ ] WebSocket 日志通道（先入库再推送）
- [ ] 前端 WebSocket 自动重连 + 历史日志拉取
- [ ] 实时执行状态展示（执行中、进度条）
- [ ] 停止执行功能（信号传递）

### Phase 8: 报告与收尾 (2 周)
- [ ] 执行快照（steps_snapshot / assertions_snapshot）
- [ ] 截图收集与存储
- [ ] HTML 报告模板生成
- [ ] 报告列表与详情页面
- [ ] 文件安全代理访问
- [ ] 重试功能（复制参数创建新 Execution）

### Phase 9: 优化与加固 (2 周)
- [ ] 变量作用域系统（全局/项目/套件/执行）
- [ ] 项目级 API Token（CI 集成）
- [ ] 报告/截图自动清理策略
- [ ] 性能优化（数据库索引、慢查询分析）
- [ ] 安全审计（渗透测试、路径遍历、SQL 注入验证）
- [ ] 文档完善（API 文档、部署手册、Agent 使用指南）

**总工期估算**：约 20.5 周（约 5 个月），配备 1 名前端 + 1 名后端 + 0.5 名测试开发（负责 Agent 与 Appium 部分）。

---

## 7. 风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| PyInstaller 打包后体积过大 | 用户下载体验差 | 使用 UPX 压缩；提供 CDN 下载；分平台按需下载 |
| PyInstaller 被杀毒软件误报 | 用户无法运行 | 购买代码签名证书（Windows EV Cert / Apple Developer ID）；加入杀毒软件白名单申请 |
| iOS 模拟器仅支持 macOS | Windows/Linux 用户无法测试 iOS | 明确平台限制；提供 macOS Agent 专用下载；企业可配置专用 Mac Mini 作为 iOS 测试节点 |
| Appium 环境配置复杂 | Agent 无法连接设备 | Agent 启动时自动检测 Appium/ADB/Xcode 环境，给出清晰错误提示和安装指引链接 |
| 高并发执行时数据库队列性能下降 | 任务消费延迟 | 为 `execution_queue` 建立适当索引；若 QPS > 100，平滑迁移到 Redis + Celery（V2） |
| 截图/日志占用磁盘过大 | 服务器磁盘满 | 实施自动清理策略（保留 30 天截图，90 天报告）；监控磁盘使用率告警 |
| 用例步骤 JSONB 结构后期变更 | 历史数据不兼容 | 版本化 steps 结构（增加 `schema_version` 字段）；迁移脚本处理旧格式 |

---

## 8. 后续扩展路线（V2 ~ V5）

| 阶段 | 触发条件 | 新增组件 | 能力 |
|------|----------|----------|------|
| **V2** | 并发执行 > 20，队列延迟明显 | Redis + Celery / Dramatiq | 分布式任务队列，多 Worker 横向扩展 |
| **V3** | 报告文件 > 100GB，多机部署 | MinIO / S3 / OSS | 对象存储，报告/截图/视频集中管理 |
| **V4** | 日志量 > 1000 万条/月，查询慢 | Elasticsearch | 日志搜索、执行链路分析、错误聚合 |
| **V5** | 团队 > 50 人，多项目高并发 | Kubernetes + 微服务拆分 | 用户服务、执行服务、设备服务独立扩展 |

---

## 9. 附录

### 9.1 项目目录结构

```
test-platform/
├── frontend/                          # Vue3 前端
│   ├── src/
│   │   ├── api/                       # Axios 封装
│   │   ├── components/
│   │   │   ├── CaseEditor/            # 用例编辑器
│   │   │   ├── StepEditor/            # 步骤编辑器
│   │   │   ├── AssertionEditor/       # 断言编辑器
│   │   │   ├── ElementSelector/       # 元素选择器
│   │   │   ├── ExecutionLog/          # 实时日志组件
│   │   │   ├── ReportViewer/          # 报告查看器
│   │   │   └── DeviceSelector/        # 设备选择器
│   │   ├── views/
│   │   │   ├── Login.vue
│   │   │   ├── Project.vue
│   │   │   ├── Element.vue
│   │   │   ├── Case.vue
│   │   │   ├── Suite.vue
│   │   │   ├── Execution.vue
│   │   │   ├── Report.vue
│   │   │   └── Device.vue
│   │   ├── stores/                    # Pinia
│   │   ├── router/
│   │   └── utils/
│   │       ├── request.ts             # Axios 拦截器
│   │       └── websocket.ts           # WebSocket 封装（重连 + 心跳）
│   ├── package.json
│   └── vite.config.ts
│
├── backend/                           # FastAPI 后端
│   ├── app/
│   │   ├── main.py
│   │   ├── api/                       # REST 路由
│   │   │   ├── auth.py
│   │   │   ├── projects.py
│   │   │   ├── modules.py
│   │   │   ├── elements.py
│   │   │   ├── cases.py
│   │   │   ├── suites.py
│   │   │   ├── executions.py
│   │   │   ├── reports.py
│   │   │   ├── devices.py
│   │   │   └── websocket.py           # WebSocket 端点
│   │   ├── models/                    # SQLAlchemy 模型
│   │   ├── schemas/                   # Pydantic 模型
│   │   ├── services/                  # 业务逻辑层
│   │   ├── executor/                  # 执行引擎
│   │   │   ├── engine.py
│   │   │   ├── context.py
│   │   │   ├── actions/
│   │   │   └── assertions/
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py            # JWT + 密码哈希
│   │   │   └── database.py
│   │   └── utils/
│   ├── worker.py                      # 独立 Worker 进程入口
│   ├── Dockerfile
│   ├── requirements.txt
│   └── alembic/                       # 数据库迁移
│
├── agent/                             # Device Agent（Python）
│   ├── main.py
│   ├── connection/
│   │   ├── ws_client.py
│   │   ├── auth.py
│   │   └── heartbeat.py
│   ├── device/
│   │   ├── android_manager.py
│   │   ├── ios_manager.py
│   │   └── device_registry.py
│   ├── appium/
│   │   ├── server_manager.py
│   │   ├── session_manager.py
│   │   └── cleanup.py
│   ├── executor/
│   │   ├── command_handler.py
│   │   ├── test_runner.py
│   │   ├── screenshot.py
│   │   └── log_forwarder.py
│   ├── utils/
│   │   ├── logger.py
│   │   └── uploader.py
│   ├── config.yaml
│   ├── build.sh                       # PyInstaller 打包脚本
│   └── Dockerfile
│
├── docker-compose.dev.yml
├── docker-compose.prod.yml
└── README.md
```

### 9.2 关键配置项

**后端 `.env` 示例**：
```env
# 数据库
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/test_platform

# JWT
JWT_SECRET_KEY=your-super-secret-key-min-32-chars
JWT_ALGORITHM=RS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=120
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# 执行
DEFAULT_EXECUTION_TIMEOUT=1800
MAX_EXECUTION_TIMEOUT=7200

# Agent
AGENT_HEARTBEAT_INTERVAL=30
AGENT_HEARTBEAT_TIMEOUT=120
MIN_AGENT_VERSION=1.0.0

# 存储
REPORTS_BASE_PATH=/data/reports
MAX_UPLOAD_SIZE=524288000  # 500MB

# 清理
REPORT_RETENTION_DAYS=90
SCREENSHOT_RETENTION_DAYS=30
LOG_RETENTION_DAYS=7
```

### 9.3 监控与告警指标

| 指标 | 阈值 | 告警方式 |
|------|------|----------|
| API 响应时间 P99 | > 500ms | 钉钉/企业微信 |
| Worker 队列堆积 | > 10 个 pending | 钉钉/企业微信 |
| Agent 离线数量 | > 0（关键 Agent） | 钉钉/企业微信 |
| 磁盘使用率 | > 80% | 钉钉/企业微信 |
| 数据库连接数 | > 80% max_connections | 钉钉/企业微信 |
| 执行失败率（1小时） | > 30% | 钉钉/企业微信 |

---

---

## 10. V1.1 增量修订（评审补全）

> 本章为设计评审后的补充设计，对正文中的冲突或缺失部分给出最终口径。**凡与本章冲突之处，以本章为准。**

### 10.1 逻辑职责与运行模式（解决执行引擎归属矛盾）

| 组件 | 职责 | 不允许做 |
|------|------|----------|
| FastAPI | 认证 / REST / WS 网关（前端 + Agent）、执行细节落库（execution_steps / execution_assertions / execution_logs）、实时广播；默认托管嵌入式 Worker runtime | 执行测试动作 |
| Worker runtime | 队列消费（SKIP LOCKED）、设备原子抢占与释放、执行状态推进、超时/心跳扫描、报告生成；可嵌入 FastAPI 或作为独立进程运行 | 独立模式下直接持有 Agent WS 连接 |
| Agent | 设备发现、Appium 会话管理、**Action / Assertion Registry 执行**、截图与文件上传 | 直接写业务库（状态/结果仅经 WS 回传） |

**关键结论**：

1. 正文 3.6 节的 Action / Assertion Registry（位于 `backend/app/executor/`）**移入 agent 包**（`agent/executor/`）。Appium driver 只存在于 Agent 侧，Worker 进程无 driver，不执行具体动作。
2. Agent 执行动作后，经 WS 将 `step_result` / `log` / `execution_result` 回传 FastAPI，**由 FastAPI 统一落库并广播**（保持"先入库再推送"原则，避免 Worker/FastAPI 双写）。
3. Worker runtime 只做调度与终态处理：轮询终态 → 生成报告 → 释放设备锁 → 队列置 done；默认 `WORKER_MODE=embedded`，由 FastAPI lifespan 启停，日常部署只启动一个 Uvicorn 进程。
4. `WORKER_MODE=external` 时 FastAPI 不启动 runtime，改由独立 `worker.py` 托管；`disabled` 仅提供 API 且不消费队列。调度与超时扫描（APScheduler）只允许一个 runtime 启用，禁止嵌入式与独立模式同时运行。
5. WS 协议修订：移除 Agent 回传的 `execution_result.report_path`（3.5.2），报告由 Worker 生成后写 `reports` 表，前端从 `/api/reports` 读取。

### 10.2 Worker ↔ Agent 通信中转（解决链路断裂）

WS 连接只存在于 FastAPI 进程内。嵌入式 Worker runtime 直接调用当前进程的 `agent_manager.send`；独立 Worker 无法直接向 Agent 发消息，继续采用**内部转发接口**：

```http
POST /internal/ws/agents/{agent_id}/send
X-Internal-Token: <配置在 .env 的内部令牌>
Body: { "type": "start_test", "execution_id": 10001, ... }
```

- 嵌入模式不产生内部 HTTP 回环；外部模式下 FastAPI 校验内部令牌后向指定 Agent 的 WS 会话转发消息，Agent 不在线返回 409。
- 反向流程（Agent → Worker 状态推进）无需 WS：Worker 每 5s 轮询 `executions.status` 是否进入终态。
- **部署约束（V1）**：多 uvicorn worker 时 WS 连接分散在各进程，内部转发可能落在不含该连接的进程。V1 明确 WS 网关以**单进程运行**（Gunicorn 仅启 1 个 Uvicorn Worker，或独立 ws-gateway 进程）；跨进程广播在 V2 引入 Redis Pub/Sub 解决。

**完整执行时序（最终口径）**：

```
前端 ──POST /api/executions/...──► FastAPI：创建 execution(QUEUED) + 入队
Worker runtime ◄──轮询── SELECT ... FOR UPDATE SKIP LOCKED（认领任务）
Worker runtime：原子抢占设备（10.5 节 SQL）
嵌入模式：Worker runtime ──agent_manager.send──► Agent
外部模式：Worker ──POST /internal/ws/agents/{aid}/send──► FastAPI ──WS──► Agent
Agent 逐步骤执行 ──WS step_result / log──► FastAPI：落库 + 广播前端
Agent ──WS execution_result──► FastAPI：更新 executions 状态
Worker runtime（每 5s 轮询）检测终态 → 生成报告 → 释放设备锁 → 队列置 done
```

### 10.3 元素定位快照补全

正文快照只存 `element_id`，若 `test_elements` 被修改，历史报告仍无法还原定位信息。`execution_cases` 增加字段：

```sql
ALTER TABLE execution_cases
    ADD COLUMN elements_snapshot JSONB NOT NULL DEFAULT '{}';
```

- Worker 创建 `execution_cases` 时，从 `test_elements` 抓取用例引用的全部元素（含 locator_type / locator_value / platform），以 `element_id` 为 key 写入 `elements_snapshot`。
- Android 混合应用可使用 `resource_id` 定位类型，`locator_value` 只保存纯 resource-id。Agent 将其转换为 `//*[@resource-id="..."]`；输入和清空动作若未显式指定可编辑子节点，自动追加 `//android.widget.EditText`，其他动作仍定位 resource-id 节点本身。
- `execution_steps.parameters` 必须保存 `steps_snapshot` 中变量渲染后的 `params`；详情聚合对历史空值从快照回退。Agent 每个步骤结束后必须上报一条 `log`，FastAPI 先写入 `execution_logs` 再广播，供执行详情实时展示并由报告聚合复用。
- `ExecutionContext.find_element`（正文 3.6.3）改为只从 `elements_snapshot` 解析，杜绝查询实时表。
- 抓取快照时按 `element_id` 直接查询（含已逻辑删除记录），避免用例引用元素被删除导致执行失败。
- 用例 `steps` 中每项增加 `phase=setup|main|teardown`，缺省为 `main`；三个阶段分别维护从 1 开始的 `order`。Worker 根据执行参数 `use_pre_steps/use_post_steps` 选择阶段，并按 setup → main → teardown 重新编号写入 `steps_snapshot`。Agent 执行顺序为前置 → 主体 → 断言 → 后置；主体或断言失败时仍尝试后置操作，后置失败会使该用例失败。

### 10.4 执行 API 请求体与停止机制

**执行创建请求体（补齐正文 3.4.7 缺失的 body 定义）**：

```json
POST /api/executions/cases/{case_id}
{
  "device_id": 12,                // 必填；由前端设备选择器传入
  "parameters": {
    "variables": { "username": "u1" },
    "use_pre_steps": true,
    "use_post_steps": true,
    "attach_to_current_app": false
  },
  "timeout_seconds": 1800
}

POST /api/executions/suites/{suite_id}   // 同结构
POST /api/executions/suites/batch
{
  "suite_ids": [1, 2],
  "device_id": 12,
  "parameters": {},
  "timeout_seconds": 1800
}
```

- `use_pre_steps/use_post_steps` 缺省均为 false；套件执行时逐个用例应用各自的前置/后置阶段。
- `attach_to_current_app` 仅允许单用例执行。为 true 时 Agent 创建不含 `appPackage/appActivity/bundleId` 的 Appium 会话，保持设备当前前台界面，并将快照中的 `launch_app` 记录为已跳过；套件和批量执行携带该参数返回 400。

**停止机制（新增 `STOPPING` 状态）**：

```
POST /api/executions/{id}/stop
→ 权限校验 → UPDATE executions SET status='stopping'
   WHERE id=? AND status='running'（原子条件更新）
→ rowcount=1 返回 200；rowcount=0 返回 409（已非运行态，无需停止）
```

- Worker 在每个步骤执行前检查 `status='stopping'` → 经内部接口向 Agent 发 `stop_test` → 当前步骤标记 stopped，未执行步骤标记 skipped，execution 置 `STOPPED`。
- Agent 收到 `stop_test` 后终止 driver 会话，回传 `execution_result(status=STOPPED)`。

### 10.5 设备原子抢占 SQL（最终口径）

Worker 认领任务后执行**原子抢占**，禁止"先查询后更新"（多 Worker 竞争会覆盖）：

```sql
UPDATE devices SET status='busy', locked_by_execution=:eid, updated_at=NOW()
WHERE id=:did AND status='idle' AND locked_by_execution IS NULL;
-- rowcount = 1 才成功；否则放弃该设备重新选择
```

设备选择策略：优先取 `agents.status='online'` 且 `status='idle'` 的设备，按 `agents.last_heartbeat DESC` 排序。执行终态后 Worker 释放：

```sql
UPDATE devices SET status='idle', locked_by_execution=NULL, updated_at=NOW()
WHERE id=:did AND locked_by_execution=:eid;
```

### 10.6 变量系统落地（补表与 API）

```sql
CREATE TABLE variables (
    id BIGSERIAL PRIMARY KEY,
    scope VARCHAR(20) NOT NULL,        -- global / project / suite / case
    project_id BIGINT REFERENCES projects(id),
    suite_id BIGINT REFERENCES test_suites(id),
    case_id BIGINT REFERENCES test_cases(id),
    name VARCHAR(100) NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    description TEXT,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_variables_scope ON variables(scope, project_id, suite_id, case_id);
```

```http
GET    /api/variables?scope=project&project_id=1
POST   /api/variables
PUT    /api/variables/{id}
DELETE /api/variables/{id}
```

渲染优先级不变：执行参数 > 套件变量 > 用例变量 > 项目变量 > 全局变量。补充规则：`render_variables` 遇到**未定义变量直接抛错**（而非静默保留 `${var}` 文本，防止定位串残留导致用例误判）。

### 10.7 截图 / 大文件改 HTTP 上传

单张截图可达 MB 级，base64 走 WS 会阻塞 Agent 通道。改为：

1. WS 通道只传通知：
```json
{"type": "file_ready", "execution_id": 10001, "file_type": "screenshot", "filename": "step_002.png", "size": 204800}
```
2. Agent 随后以 HTTP 上传：
```http
POST /api/agent/upload
X-Agent-Key: sk-...        # PSK 认证
Body: multipart/form-data (file)
```
3. 服务端校验扩展名与大小（截图 ≤ 10MB）后存入 `/data/reports/2026/08/20/execution_10001/`，返回相对路径，由 Worker 更新对应 `execution_steps.screenshot_path`。

### 10.8 技术选型修正

| 原方案 | 修正 | 原因 |
|--------|------|------|
| python-jose | **PyJWT 2.8+** | python-jose 维护停滞、CVEs 多 |
| passlib + Argon2 | **argon2-cffi 直接使用** | passlib 1.7.4 与新版 argon2-cffi 不兼容 |
| JWT RS256 + JWT_SECRET_KEY | V1 统一 **HS256**（`JWT_SECRET_KEY` ≥ 32 字符随机）；升级 RS256 需改配 PRIVATE_KEY/PUBLIC_KEY 路径，与 `.env` 二选一 | 消除正文 4.1 与 9.2 的配置矛盾 |
| `status` 大小写混用 | 统一**全小写枚举**：`queued / running / stopping / passed / failed / error / stopped / cancelled`，DB、API、WS 消息一致（正文状态机图大写仅为示意） | 前后端一致性 |

### 10.9 队列回收与细节补丁

1. **claimed 行回收**：APScheduler（Worker 内）每 60s 将 `status='claimed' AND claimed_at < NOW() - interval '10 min'` 且对应 execution 未处于 running 的队列行重置为 `pending`（`retry_count+1`；≥3 次置 `failed`，人工介入）。防止 Worker 崩溃导致任务永久卡死。
2. **Refresh Token 黑名单**：新增轻量表 `refresh_tokens(id, token_hash, user_id, expires_at, revoked_at)`，登出时写入 `revoked_at`。V1 必需（否则登出无法真正失效 Token）。
3. **清理策略落地**：提供 `scripts/cleanup_reports.py`（按 9.2 配置的保留天数删除文件并同步 `reports` 记录），由 Worker 内 APScheduler 每日执行。
4. **目录命名统一**：`fronted/` → `frontend/`、`device-agent/` → `agent/`，与 9.1 目录结构一致。
5. **APK 上传（V1 可选）**：`POST /api/projects/{project_id}/apps`（限 .apk/.ipa，≤500MB）；执行参数支持 `app_upload_id`，Agent 经 HTTP 拉取安装。V1 缺省假设应用已预装，此项列入 V2。

### 10.10 修订版状态机（含 STOPPING）

```
QUEUED
  │
  ▼
RUNNING ←── Worker 认领后经内部接口通知 FastAPI 更新
  │
  ├── 正常完成 ────► PASSED
  ├── 断言失败 ────► FAILED
  ├── 执行异常 ────► ERROR
  ├── 用户停止 ────► STOPPING ──► STOPPED
  └── 超时/Agent 失联 ─► ERROR（超时引擎触发）
```

禁止反向转换；重试必须创建新 Execution 并关联 `retry_of`。

---

### 10.11 Windows Agent 安装与多用户绑定（V1.2 增量）

> 依据《Windows_Agent安装与设备管理实施方案.md》（V1.0）实施后固化，为最新口径。
> 涉及表：`user_agent_keys`、`agent_users`、`device_preferences`；`devices.connection_type/address`；`executions.stop_requested_at/finalized_at`。

1. **多用户绑定与权限**：
   - 每用户一条专属 Key `uak_<public_id>_<secret>`（public_id 为 hex，secret 可含下划线）；库中仅存 Argon2 哈希 + Fernet 密文（`AGENT_USER_KEY_ENCRYPTION_KEY` 派生密钥），明文仅 GET `/api/me/agent-key` 解密返回。
   - `POST /api/agent/bind`：首绑（user_key+install_id）创建 Agent 与机器 PSK；追加绑定（machine_psk+另一用户 Key）只加 `agent_users`。机器 PSK 与撤销凭据只返回一次，用户 Key 不以明文写入服务端数据库或日志；桌面 Agent 在绑定成功后可将最近使用的 Key 明文保存为 EXE 运行目录下的 `user_key.txt`，启动时仅以掩码回填。
   - 普通用户只可见/可用其绑定 Agent 下的资源（`/agents`、`/devices`、详情、执行均校验）；平台管理员不受限。
   - 解绑：机器侧 `DELETE /api/agent/bindings/{id}`（机器 PSK+撤销凭据）；用户侧 `DELETE /api/agents/{id}/bindings/me`。
2. **设备与执行**：
   - `devices.connection_type`（usb/wifi）、`devices.address`（无线地址）；`GET/PUT /api/devices/default` 为用户级默认设备（含实时可用性 reason）。
   - 执行创建 `device_id` **必填**，缺失返回 `DEVICE_REQUIRED`；创建/重试前校验设备授权、Agent 在线、设备 idle 未锁；Worker 不再从全平台设备池随机选机，只原子锁指定设备（条件 UPDATE）。
   - Agent 快照不得覆盖 busy 锁状态（`busy && locked_by_execution` 时跳过）。
   - 运行入口自动选机：默认设备可用→直跑；不可用→弹窗（仅在线空闲+有权限）；无可选→下载/绑定/连接引导；并发占用（`DEVICE_BUSY`/`AGENT_OFFLINE`）→提示并刷新，不自动换设备。
3. **Windows Agent**：
   - 交付 Inno Setup 安装包（LocalAppData 安装、HKCU 登录自启动、免管理员）；托盘（pystray）+ Tkinter 管理窗口；asyncio 网络循环在后台线程。
   - `adb devices -l` 每 3s 轮询（工作线程），设备集合变化立即上报 `device_list`，无变化每 30s 全量；`unauthorized`→提示允许 USB 调试，`offline`→连接异常，仅 `device` 上报 idle。
   - 机器 PSK/撤销凭据存 Windows Credential Manager（回退 LocalAppData 文件）；最近使用的用户 Key 按桌面交互要求明文保存在运行目录 `user_key.txt`，输入框默认掩码；Appium 按需隐藏启动（127.0.0.1），执行结束/退出清理 Session 与子进程树。
   - 安装包托管于后端 `AGENT_RELEASES_PATH`：`latest.json`（version/filename/sha256/size/published_at）+ 5 分钟限定文件名下载 JWT + FileResponse 流式下载（防路径穿越）。
4. **执行时间戳**：
   - `stop_requested_at`：用户请求停止时刻（queued 取消与 running→stopping 均写入）；停止宽限期从此起算（`execution_stop_grace_seconds`），无值时回退 started_at+timeout 口径。
   - `finalized_at`：唯一终态汇总完成时刻（`_mark_terminal`/queued 取消写入）。

---

### 10.12 多 APP 档案与差异化执行（V1.3 增量）

> 依据《多APP形态共用测试资产与差异化执行_详细实施方案.md》固化；本节与前文冲突时以本节为准。

1. **测试资产口径**：测试套件、用例、步骤、断言、元素和变量仍只有一份公共资产；`app_profiles` 只保存差异规则，不复制套件。步骤和断言的 JSON `key` 必须是稳定且唯一的 UUID，排序和改名不得改变 key。
2. **档案与版本**：每个项目可建多个 `app_profiles`，发布版本由 `app_profile_releases` 管理。迁移为活动项目幂等创建“通用配置（待调整）/未标注历史版本”。`profile.revision` 只在有效规则变化时递增；公共测试资产变化递增 `projects.test_asset_revision`。
3. **差异规则**：`app_profile_skip_rules` 支持 suite/case/step/assertion 四级跳过，父级规则优先；`app_profile_element_overrides`、`app_profile_variable_overrides`、`app_profile_node_overrides` 分别覆盖定位、变量和 Registry 允许的节点参数。所有写命令携带 `expected_revision` 与 `request_id`，Owner/Admin 可写，项目成员只读。
4. **执行快照**：公共库运行必须选择档案和活动发布版本并调用 `POST /api/executions/preview`。正式提交携带 `app_profile_id`、`app_release_id`、`expected_profile_revision`、`expected_test_asset_revision`；服务端在同一事务内二次锁定双 revisions、生成完整执行快照、固化 `execution_exclusions` 并入队。Agent 只接收最终快照，不解析档案规则。
5. **报告口径**：执行记录永久保存档案名、版本、双 revisions 和解析摘要快照；N/A 来自 `execution_exclusions`，不计入成功率分母，运行期 skipped 与 N/A 分开展示。历史 `app_profile_id IS NULL` 的执行显示“历史兼容执行”，不得查询当前配置回填历史结果。
6. **灰度与回滚**：`APP_PROFILE_FEATURE_MODE=off|compat|required`；`compat` 仅对 `APP_PROFILE_ENABLED_PROJECT_IDS` 中的项目将旧请求注入通用档案，`required` 要求所有新请求显式选择档案/版本，`off` 保持旧执行协议。关闭灰度不删除档案、审计或历史快照。
7. **核心接口**：档案 `/api/projects/{id}/app-profiles`，版本 `/api/app-profiles/{id}/releases`，规则 `/api/app-profiles/{id}/skip-rules/batch`，覆盖 `/api/app-profiles/{id}/*-overrides`，工作台 `/api/app-profiles/{id}/workspace`，差异清单 `/api/app-profiles/{id}/differences`，预检 `/api/executions/preview`；报告列表支持 `app_profile_id/app_release_id` 筛选。

---

### 10.13 Android 动态元素智能定位（V1.4 增量）

> 本节为智能定位（smart locator）最终口径；与前文 §3.6 元素定位、§10.3 元素快照冲突时以本节为准。
> 涉及：`test_elements.locator_config`、`app_profile_element_overrides.locator_config`、执行快照 `elements_snapshot`、Agent `ElementResolver`、前端智能定位编辑器。
> 范围：仅 Android/Appium UiAutomator2；不实现 iOS、OCR、图像识别与实时设备“测试定位”接口。保留既有 id/resource_id/xpath/accessibility_id 等普通定位能力。

1. **元素模型**：`test_elements` 与 `app_profile_element_overrides` 各新增 `locator_config JSONB`（可空）；`locator_value` 改为可空。DB CHECK 约束固化判别关系（`ck_test_elements_locator_mode` / `ck_profile_element_locator_mode`）：
   `(locator_type='smart' AND locator_config IS NOT NULL AND locator_value IS NULL) OR (locator_type<>'smart' AND locator_config IS NULL AND locator_value IS NOT NULL)`。
   覆盖语义为**整体替换** locator_type/locator_value/locator_config 三字段。
2. **配置协议**：`locator_config` 为强类型 JSON（后端 Pydantic + Agent 自校验双保险）：
   - `version: 1`
   - `alternatives: 1..10`，按顺序执行；每项含 可选 `anchor`（锚点条件，1..20 条）、可选 `path`（相对路径，1..3 段）、必填 `target`（匹配条件，1..20 条，同为 AND）
   - 条件 `{attribute, operator, value}`：attribute 白名单 `text/content_desc/resource_id/class_name/package/clickable/enabled/selected/displayed`；operator `equals/contains/starts_with/ends_with/regex`；布尔属性（clickable/enabled/selected/displayed）value 必须为 bool 且 operator 仅 `equals`；字符串值 1..200 字符，regex 必须可编译
   - 相对路径段 `{axis, depth?}`：axis `parent/ancestor/child/descendant/following_sibling/preceding_sibling`；`ancestor/parent` 必须显式 depth（1..5，第 N 级祖先；depth≥2 按 `ancestor::*[N]` 生成），其余轴不得设置 depth
   - `search`：`scroll: true`、`direction: up|down`、`max_swipes: 1..20`（全部候选共享预算）、`duration_ms: 100..2000`、`settle_ms: 0..2000`；滚动为**全屏滑动**（`driver.swipe`），不支持指定滚动容器
   - `selection`：默认 `policy='unique'`（匹配>1 立即失败，禁止自动退化为第一个）；`policy='index'` 必须显式 `index ≥ 1`（1 基：index=1 即第 1 个匹配；越界即失败）
   - 安全：协议结构不接受任何原始 XPath / UiAutomator 表达式；组合/相对定位由 Agent 经安全转义后生成。正则仅校验可编译与长度上限，**不设复杂度/ReDoS 防护**，生产使用需注意
3. **变量与快照**：`locator_config` 内 `${variable}` **保留原样写入快照**（后端 profile_resolver/worker_service 不渲染），由 Agent 执行时用执行参数 `variables` 渲染；渲染后 Agent 重新校验长度/正则。普通元素 `locator_value` 仍由后端渲染。快照元素条目统一结构：`{name, platform, locator_type, locator_value, locator_config}`，`platform` 供 Agent 平台守卫使用。
4. **Agent 解析语义**：
   - 自校验 → 渲染变量 → 逐候选：0 匹配→滚动循环后仍 0→下一候选；1 匹配→按 selection 返回；>1 且 unique→立即 `ElementNotUnique`（不试后续候选）；index 显式取第 N 个，匹配数不足→失败
   - 选择器双策略：无 anchor/path 的普通候选用 UiAutomator `UiSelector` 链（可含 regex）；含相对定位/ends_with/displayed 等无法用 UiSelector 表达的用 XPath（字面量安全转义）；regex 出现在 anchor/path 场景→`InvalidSmartLocator`，regex 亦不得与 ends_with/displayed 等需 XPath 表达的条件组合
   - 滚动：每次滑动后等待 `settle_ms` 再查询；页面指纹（page_source 摘要哈希，不记录完整源码）连续两次不变→提前停止；预算耗尽→`ScrollLimitReached`；全程 60s 总超时；每次查询/滑动前检查用户停止信号
   - 平台守卫：非 Android（ios 等）`platform` 直接拒绝（`InvalidSmartLocator`，消息明确“仅支持 Android”）
5. **统一操作接线**：click/input/clear/get_text/get_attribute 与全部元素断言统一经 `with_stale_retry`：遇 Stale 异常→丢弃旧元素→按原智能规则重新定位→重试，最多 2 次，耗尽→`ElementStaleRetryExhausted`；不新增 find_and_click 等孤立动作。
6. **错误类型**：Agent 异常类 `InvalidSmartLocator / ElementNotFound / ElementNotUnique / ScrollLimitReached / ElementStaleRetryExhausted`（无机器错误码，经 `step_result.error_message` 中文文本上报落库）；日志含候选规则序号、匹配数量、滚动次数、失败原因，不记录完整页面源码。
7. **前端**：元素库「智能定位（Android）」可视化编辑器（候选规则/锚点/相对路径/滚动/匹配策略，实时校验，索引策略带风险提示）；APP 档案覆盖抽屉复用同一编辑器组件；列表展示规则摘要（如「文字等于 ${device_name} + 类名等于TextView + 向上滑动8次」）。前端校验覆盖面为结构与数值上限；组合语义限制（regex 与 anchor/path 及需 XPath 表达的条件互斥、path 须带 anchor）由 Agent 运行时校验。
8. **协议版本**：elements_snapshot 经 start_test 载荷下发（非 WS 入站消息），无新增 action/assertion Registry 项，`protocol_version` 不提升，协议产物无需重新生成（`generate_protocol.py --check` 通过）。

---

> **文档结束**。本方案基于原始设计进行了系统性修订，重点解决了执行引擎耦合、Agent 落地性、执行可靠性、报告可追溯性等核心问题，并经由 V1.1 评审补齐执行职责划分、Worker↔Agent 通信中转、元素快照、停止机制、设备原子锁、变量系统等缺口，可直接作为项目启动的技术基线。
