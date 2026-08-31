# Windows Agent 安装与设备管理 —— 代码实施步骤设计

> 依据：`docs/Windows_Agent安装与设备管理实施方案.md`（V1.0，6 个实施阶段）
> 日期：2026-08-22
> 状态：设计稿（待逐阶段执行）

## 0. 现状盘点（与方案前置整改的差距）

| 方案前置整改项 | 现状 | 结论 |
|---|---|---|
| Agent WS→HTTP 地址转换、截图上传链路 | `agent/main.py:http_base_url`、`agent/uploader.py`、`executor/test_runner.py:_resolve_screenshot` 已完成 | ✅ 无需再做 |
| ADB/Appium 同步调用阻塞事件循环 | `main.py:discover_devices` 用 `subprocess.run` 同步调用；Appium 驱动同步执行 | ❌ 待改 |
| `executions.stop_requested_at` | 模型/迁移无此列 | ❌ 待加 |
| `executions.finalized_at` | 模型/迁移无此列 | ❌ 待加 |
| Worker 数据库行锁唯一终态汇总 | `worker_service._mark_terminal` + Report `on_conflict_do_nothing` | ✅ 已完成 |
| queued 取消与 Worker 认领竞争 | `stop_execution` 条件 UPDATE 单赢家 | ✅ 已完成 |
| 用例 NaN / 元素字段清空 / 嵌套校验 | Phase C 已完成 | ✅ 已完成 |
| 历史唯一键清理 + admin 提升迁移 | 87a7005420f3 已清理 PSK；seed 幂等需核验 | ⚠️ 核验 seed |
| 独立 `test_platform_test` 测试库 | conftest 仍直连 dev 库 | ❌ 待改 |
| CI 前端测试步骤 | ci.yml 前端只有 build，无 test | ❌ 待加 |

**结论**：方案 Step 1（前置整改）剩余约 4 项；Step 2 起为全新功能。

---

## Step 1：前置整改收尾（后端 + Agent + CI）

### 1.1 `executions.stop_requested_at` / `finalized_at`
- `backend/app/models/execution.py`：Execution 增加
  - `stop_requested_at: Mapped[datetime | None]`（DateTime(timezone=True)）
  - `finalized_at: Mapped[datetime | None]`
- 迁移：`uv run alembic revision --autogenerate -m "add executions stop_requested_at finalized_at"`，审阅后 upgrade。
- `backend/app/services/execution_service.py`：
  - `stop_execution`：running→stopping 时同时写 `stop_requested_at=now(UTC)`；queued→cancelled 时也写（终态）。
- `backend/app/services/worker_service.py`：
  - `_mark_terminal`（及超时/心跳终态路径）统一写 `finalized_at=now(UTC)`；
  - `timeout_scan` / poll 循环的停止宽限期改为从 `stop_requested_at` 计算（`execution_stop_grace_seconds`），无 stop_requested_at 时退化为 started_at。
- `backend/app/schemas/execution.py`：ExecutionOut/Detail 暴露两字段。
- 测试：`tests/test_executions.py` 补 stop 后 `stop_requested_at` 非空；worker 测试补 `finalized_at` 断言。

### 1.2 Agent 非阻塞化
- `agent/main.py`：`discover_devices` 改为 `await asyncio.to_thread(...)` 内执行 subprocess，超时 10s；
- `agent/executor/appium_driver.py`：所有同步 Appium 调用（session 创建、command）包到 `asyncio.to_thread` 或专用线程池；`interrupt()` 在独立线程关闭 session 并置取消事件；
- `agent/executor/test_runner.py`：动作执行挂到线程池，停止时事件轮询粒度 <1s；
- `agent/tests/test_executor.py`：补"阻塞调用不卡事件循环"测试（driver 模拟慢操作 + interrupt 后事件循环仍响应）。

### 1.3 独立测试库 `test_platform_test`
- `backend/app/core/config.py`：新增 `test_database_url`（默认 `postgresql+asyncpg://dev:dev123@127.0.0.1:5432/test_platform_test`）。
- `backend/tests/conftest.py`：
  - session 级 fixture：连 `test_database_url` 建库（若不存在 `CREATE DATABASE test_platform_test`）→ 跑 `alembic upgrade head`（或 `Base.metadata.create_all` 改用 alembic）→ 测试结束后可选清库；
  - 启动时断言当前 `database_url` 指向测试库（`"test_platform_test" in url`），否则 pytest 直接报错拒绝运行（方案 §2"拒绝在非测试库运行"）。
- `backend/pyproject.toml` 或 pytest.ini：`env` 注入 `DATABASE_URL=test_database_url`（用 `pytest-env` 或 conftest 内 monkeypatch 更稳：在 import app 前设置 env）。
- 各测试原有 `pytest_%` 清理逻辑保留，但追加清理 `UserAgentKey / AgentUser / DevicePreference`（Step 2 新增表）。
- 本地需先 `createdb test_platform_test`（`D:\Program Files\pg\bin\createdb`），并把测试库跑一次迁移。

### 1.4 admin 提升迁移 + seed 幂等
- 核验 `backend/app/seed.py`：已存在 admin 时更新 `is_admin=True` 而非报错；
- 迁移（如 6a7fb396ae24 未包含）：把现有首个用户/`admin` 用户名用户提升为管理员（`UPDATE users SET is_admin=TRUE WHERE username='admin'`，幂等）。

### 1.5 CI
- `.github/workflows/ci.yml` frontend job 增加 `npm test`（vitest run）步骤；backend job 数据库服务改为建 `test_platform_test`（POSTGRES_DB 两次或 init 脚本），跑迁移后 pytest 使用测试库。

**提交**：`feat(backend): Step 1 执行时间戳与测试库隔离整改` + `feat(agent): Step 1 非阻塞 ADB/Appium` + `chore(ci): Step 1 前端测试步骤`。
**验收**：backend ruff + 全量 pytest（测试库）、agent ruff + pytest、前端 test + build、alembic check 全部通过。

---

## Step 2：多用户绑定与设备权限（后端主体）

### 2.1 数据模型与迁移
- `backend/app/models/agent.py`（或新 `backend/app/models/agent_binding.py`）：
  - `UserAgentKey`：`user_id`(FK unique)、`public_id`(String, unique)、`key_hash`(Argon2，存 `uak_<public_id>_<secret>` 整体或 secret 部分)、`encrypted_secret`(Text, Fernet)、`created_at/updated_at`（TimestampMixin）。
  - `AgentUser`：`agent_id`(FK)、`user_id`(FK)、`revoke_credential_hash`(String)、`created_at`；`UniqueConstraint(agent_id, user_id)`。
  - `DevicePreference`：`user_id`(FK unique)、`device_id`(FK)；设备删除时置空（FK ondelete 处理 + service 逻辑）。
  - `Device` 增加：`connection_type: String(10) default 'usb'`（usb/wifi）、`address: String(255) nullable`。
- `backend/app/models/__init__.py` 导出新模型；迁移一条 `add user_agent_keys agent_users device_preferences device conn fields`。

### 2.2 Key 加密与安全
- `backend/app/core/config.py`：新增 `agent_user_key_encryption_key: str = ""`；`validate_security_baseline()` 在 production 要求非空且 >=32 字符。
- `backend/app/core/security.py`：
  - `new_user_agent_key() -> (public_id, secret, full_key)`：`uak_<public_id>_<secret>`，secret 用 `secrets.token_urlsafe(32)`；
  - `encrypt_user_key/decrypt_user_key`：Fernet（key 由配置派生，`base64.urlsafe_b64encode(sha256(...))`）；
  - `hash_user_key/verify_user_key`：Argon2（复用 `hash_psk` 风格）。

### 2.3 接口（新 `backend/app/api/me.py` + 扩充 `agent.py`/`agents.py`）
- `GET /api/me/agent-key`：解密返回 `{public_id, key}`；不存在返回 `{exists: false}`。
- `POST /api/me/agent-key`：首次生成；已存在返回 409。
- `POST /api/me/agent-key/regenerate`：生成新 Key（旧 Key 不可再新增绑定）；不删已有 `agent_users`。
- `POST /api/agent/bind`（**限流：按 IP + 按 public_id**）：
  - body：`{user_key, install_id, hostname, version, platform}`，绑定请求不携带机器 PSK；
  - 首次绑定：校验 `user_key` → 按 `install_id` 创建 `Agent`、生成机器 PSK、创建 `AgentUser` + 撤销凭据并返回；
  - 后续、恢复或重复绑定：只校验 `user_key`，创建或保留 `AgentUser`，同时旋转机器 PSK 和当前用户撤销凭据并返回；旧机器 PSK 不参与绑定授权且立即失效；
  - 原始 user_key 不写日志（log 中脱敏 `uak_***`）。
- `GET /api/agent/bindings`（机器 PSK 认证）：返回该 Agent 已绑定用户名列表。
- `DELETE /api/agent/bindings/{id}`（机器 PSK + revoke_credential 认证）：解绑。
- `DELETE /api/agents/{id}/bindings/me`（用户 JWT）：撤销自己绑定。
- `backend/app/api/deps.py`：新增 `require_agent_access(agent, user)`（admin 或存在 `AgentUser`），`require_device_access(device, user)`。

### 2.4 设备/Agent 查询权限与默认设备
- `backend/app/api/agents.py`：`list_agents` / `list_devices` / 详情接口——非管理员只返回 `AgentUser.user_id == user.id` 的 Agent 及其设备；管理员返回全部。
- `GET /api/devices/default`：返回 `{device_id, device, available: bool, reason}`（权限/离线/busy/锁）。
- `PUT /api/devices/default`：`{device_id | null}`；只能设置本用户有权限的设备。
- 执行创建/重试：`device_id` 改为必填；缺失返回 400 `{code: "DEVICE_REQUIRED"}`；每次创建前校验 `require_device_access` + Agent 在线 + 设备 idle 未锁；`worker_service.select_and_lock_device` 移除全平台随机兜底分支（`execution.device_id` 为 None 时直接报错/跳过）。
- `backend/app/ws/handlers.py`：`handle_device_list` 快照更新时，`status == 'busy'`（被锁）设备不允许普通快照覆盖（保留 busy + 保留 locked_by_execution）。

### 2.5 测试
- `tests/test_agent_binding.py`：Key 生成/查看/重置、首次/重复/恢复绑定、追加绑定（第二用户）、绑定后机器 PSK 旋转、解绑（双方）、管理员可见、第三用户不可见、限流 429、后续机器通信 PSK 认证失败。
- `tests/test_device_permission.py`：越权设备访问 403/404、默认设备设置/清除/越权、DEVICE_REQUIRED、busy 快照不被覆盖。
- 权限矩阵测试扩充：普通用户看不到他人 Agent/设备。

**提交**：`feat(backend): Step 2 多用户绑定与设备权限`（可拆 2-3 个提交：模型迁移 / Key 与绑定接口 / 权限与默认设备）。
**验收**：ruff + 全量 pytest（含新矩阵测试）+ alembic check。

---

## Step 3：本地设备管理与 Agent 核心

### 3.1 模块拆分（`agent/`）
- `agent/connection.py`（从 ws_client.py 拆出）：WS 连接管理 + 心跳；
- `agent/devices/registry.py`：ADB 设备注册表——轮询、变更检测、快照缓存、授权状态；
- `agent/devices/adb.py`：`adb devices -l` / `pair` / `connect` / `disconnect` 参数数组调用（`subprocess.Popen(args, shell=False)`）、15s 超时、`unauthorized→"请在设备上允许 USB 调试"`、`offline→"连接异常"` 错误翻译、主机/IP+端口校验（`ipaddress`）；
- `agent/appium_lifecycle.py`：Appium 隐藏启动（`127.0.0.1:4723` 默认、端口可配）、session 创建/清理、子进程树终止；
- `agent/executor/` 保持 Registry 执行，改为经线程池非阻塞（Step 1.2 已做）；
- `agent/credentials.py`：Windows Credential Manager（`ctypes`/`win32cred` 封装，无第三方依赖优先）保存机器 PSK 与各用户撤销凭据；测试环境用文件 fallback；
- `agent/desktop/`（Step 4 托盘，先留接口 `controller.py` 占位）。

### 3.2 扫描与上报
- 轮询周期：`adb devices -l` 每 3s；设备集合变化（增/删/状态变）→ 立即 `device_list`；无变化每 30s 一次全量快照（方案 §4.1）；
- 快照字段：`udid/name/address/connection_type/platform/platform_version/device_type/status`（status 仅 `device` 上报 idle，`unauthorized/offline` 如实上报）；
- 被执行锁定的设备（registry 里标记 busy）不上报覆盖锁状态。

### 3.3 绑定管理（Agent 侧）
- 启动时从 Credential Manager 恢复机器 PSK → `GET /api/agent/bindings` 拉取已绑定用户；
- 用户在本机录入 `uak_...` Key → `POST /api/agent/bind`（后续绑定）→ 保存撤销凭据，并将最近使用的 Key 明文写入运行目录 `user_key.txt` 供掩码回填；
- 解绑：`DELETE /api/agent/bindings/{id}`。

### 3.4 测试
- `agent/tests/test_adb.py`：输出解析（USB/Wi-Fi/unauthorized/offline）、pair/connect/disconnect 参数与超时、错误翻译、非法地址拒绝；
- `agent/tests/test_registry.py`：热插拔变更检测（fake adb）、快照节流（3s/30s）；
- `agent/tests/test_credentials.py`：凭据保存/恢复/丢失；
- `agent/tests/test_binding.py`：多用户绑定/解绑流程（mock server）；
- `agent/tests/test_appium_lifecycle.py`：启动/清理、阻塞停止。

**提交**：`feat(agent): Step 3 设备注册表/ADB/Appium 生命周期/绑定`（拆 2-3 个提交）。
**验收**：agent ruff + 全量 pytest；mock 模式 `uv run python main.py --config config.yaml` 联调设备快照。

---

## Step 4：托盘界面与 Windows 安装包

### 4.1 桌面控制器（`agent/desktop/`）
- `agent/desktop/tray.py`：pystray 托盘（图标、菜单：打开窗口/连接无线设备/退出）；
- `agent/desktop/window.py`：Tkinter 主窗——卡片式服务器连接、Agent ID/版本/在线/Appium 状态；已绑定用户列表 + 默认掩码且可显示/隐藏的 Key 输入框（绑定成功后明文保存到运行目录 `user_key.txt`）+ 绑定/解绑；带设备计数的设备表；"连接无线设备"对话框（地址/端口 + 可选配对地址/端口/配对码）；手动刷新、无线断开、打开日志目录；
- `agent/desktop/app.py`：主入口——启动 asyncio 网络循环于独立后台线程（`asyncio.run` in thread），Tkinter 主循环在 UI 线程；UI 与网络层经线程安全队列通信；
- 程序数据/日志/临时截图放 `%LOCALAPPDATA%\AppAutoTestAgent\`；日志按大小轮转（`logging.handlers.RotatingFileHandler`，如 2MB×5）。

### 4.2 打包与自检
- `agent/packaging/pyinstaller.spec`：PyInstaller `onedir`（隐藏导入 pystray/Tkinter，排除测试）；
- `agent/packaging/setup.iss`：Inno Setup 版本化 `app-auto-test-agent-<version>-windows-x64-setup.exe`；当前用户安装到 LocalAppData；创建开始菜单/卸载项；HKCU `Run` 登录自启动；安装完成启动托盘 Agent；无需管理员权限；
- `agent/main.py` 增加 `--self-check`：校验内置 adb 存在、Appium 依赖可导入、配置可读、可写 LocalAppData，输出结构化结果；
- `agent/packaging/publish.ps1`：跑 PyInstaller → Inno 编译 → 计算 SHA-256 → 生成 `latest.json` → 复制到发布目录（Step 5 的 `AGENT_RELEASES_PATH`）。

### 4.3 Windows CI
- `.github/workflows/ci.yml` 新增 `windows-agent-package` job（windows-latest）：agent pytest → `--self-check` → PyInstaller → Inno Setup 编译 → SHA-256 manifest；`AGENT_SIGN_CERT` 存在时执行 Authenticode 签名，否则跳过（输出 SmartScreen 提示说明）。

**提交**：`feat(agent): Step 4 托盘界面与打包`。
**验收**：本机 `publish.ps1` 产出安装包，安装/卸载/自启动/托盘可用；CI Windows job 通过。

---

## Step 5：后端分发与前端自动选机

### 5.1 安装包下载
- `backend/app/core/config.py`：`agent_releases_path: str = "./data/agent-releases"`（production 必填，加入基线校验）。
- `backend/app/services/release_service.py`：
  - `read_manifest()`：读 `latest.json`（`version/filename/sha256/size/published_at`）；
  - `issue_download_token(filename)`：JWT `sub=filename`、`exp=now+5min`（`jwt_secret_key` 签发）。
- `backend/app/api/releases.py`：
  - `GET /api/agent/releases/latest`（登录）：manifest；
  - `POST /api/agent/releases/latest/download-token`：`{token}`；
  - `GET /api/agent/releases/download/{filename}?token=`：校验 token 且 `sub==filename`，`resolve` 后必须仍在发布目录内（防穿越），`FileResponse`（`Content-Disposition: attachment`，支持 Range——Starlette FileResponse 已支持）。
- `backend/app/main.py` 注册路由；`backend/scripts/publish_release.ps1`：复制安装包 + 原子更新 manifest（先写临时文件再 rename）。
- 测试：`tests/test_releases.py`：latest 元数据、token 过期/错文件名 403、路径穿越拒绝、下载内容与 sha256 一致。

### 5.2 前端设备页重构（`frontend/src/views/Device.vue` → 三区域）
- 区域一"Agent 下载与 Key"：展示个人 Key（脱敏可复制）、生成/重置；下载按钮（拉 latest → 弹窗显示版本/大小/SHA-256 → 取 token → `window.open(downloadUrl)`）；
- 区域二"已绑定 Agent"：表格（Agent ID、在线状态、版本、主机名、设备数；管理员可见全部 + 撤销绑定按钮，普通用户仅自己绑定的 + 撤销自己的绑定）；
- 区域三"我的设备"：默认设备标记（星标）、设为默认/取消默认、状态、连接类型（USB/Wi-Fi）。
- `frontend/src/api/me.ts`（新）、`frontend/src/api/releases.ts`（新）、扩充 `agents.ts`。

### 5.3 运行入口自动选机
- 运行用例/套件/批量套件（Case.vue / Suite.vue / Project.vue 中的运行按钮）统一走 `frontend/src/composables/useDeviceSelect.ts`：
  1. `GET /api/devices/default`；
  2. 可用 → 直接创建执行 → 跳执行详情；
  3. 未设置/忙碌/离线/失权 → 弹窗（只列有权+在线+idle 设备，含"设为默认"勾选）→ 选中后创建；
  4. 无可选 → 引导弹窗（下载安装 Agent、绑定 Key、连接设备），不提交空 `device_id`；
  5. 创建返回 `DEVICE_REQUIRED` / 409（并发占用）→ 提示"设备刚被其他执行占用"，刷新列表，不自动换设备。
- 执行创建请求始终携带 `device_id`。

### 5.4 前端测试
- `frontend/src/__tests__/device-select.test.ts`：默认设备直跑、不可用弹窗、并发占用提示、DEVICE_REQUIRED 守卫。

**提交**：`feat(backend): Step 5 安装包分发与下载令牌` + `feat(frontend): Step 5 设备页重构与自动选机`。
**验收**：backend ruff + pytest、前端 test + build、发布目录放一个测试安装包后全流程联调。

---

## Step 6：完整验收

1. 独立测试库执行后端全量测试；
2. agent / 前端 / alembic upgrade+check / Windows 安装包验证（`--self-check`）；
3. 真实 Android 设备：USB 发现 → 绑定 → 默认设备直跑 → 执行 → 停止（宽限期）→ 截图上传 → 报告生成；
4. Wi-Fi：`adb pair` + `adb connect` → 设备在线 → 执行；
5. 双用户绑定同一 Agent 的可见性；双用户抢同一设备的单赢家。

---

## 执行顺序与依赖

```
Step 1（后端时间戳+测试库 | agent 非阻塞 | CI）  ← 无前置
Step 2（绑定/权限/默认设备/选机收紧）             ← 依赖 Step 1 测试库
Step 3（agent 设备注册表/ADB/Appium/凭据）        ← 依赖 Step 2 绑定接口
Step 4（托盘 + 打包 + Windows CI）                ← 依赖 Step 3
Step 5（分发 + 前端自动选机）                     ← 依赖 Step 2 接口 + Step 4 安装包
Step 6（全链路验收）                              ← 依赖全部
```

每个 Step 独立提交，提交规范 `feat(backend|agent|frontend|ci): Step N <内容>`。
