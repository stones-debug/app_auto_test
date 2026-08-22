# Windows Agent 安装、设备管理与自动选机实施方案

> 版本：V1.0  
> 日期：2026-08-21  
> 状态：待实施  
> 范围：Windows x64、Android、Agent 安装与分发、多用户绑定、设备同步和执行选机

## 1. 总体方案

- 第一版仅支持 Windows x64 + Android；USB 设备自动发现，Wi-Fi 设备支持 `adb pair` 和 `adb connect`，不支持 Windows 上的 iOS。
- Agent 交付为 Inno Setup 生成的完整 EXE 安装包，按当前 Windows 用户安装到 LocalAppData，内置 Python Agent、ADB platform-tools、便携 Node、Appium Server 和 UiAutomator2 Driver。
- Agent 登录 Windows 后自动启动并常驻系统托盘；双击托盘图标打开本地管理窗口。
- 安装包内置默认服务器地址，Agent 设置页允许修改；HTTP、WebSocket 和上传地址统一从该地址可靠派生。
- 安装包及 `latest.json` 放在后端配置的发布目录，后端流式提供下载。暂不自动升级，用户人工重新下载安装。
- 实施时同步更新权威架构文档，明确新的多用户绑定、设备归属、选机和 Windows 安装口径。

## 2. 前置整改

先处理上一轮所有验收阻断项，再开发新功能：

- 修复 Agent WebSocket→HTTP 地址转换、截图上传链路，以及所有 ADB/Appium 同步调用阻塞事件循环的问题；阻塞命令统一进入工作线程，停止时设置取消事件并关闭 Appium Session。
- 为执行增加 `stop_requested_at`，停止宽限期从用户请求停止时计算。
- 为执行增加 `finalized_at`；Worker 使用数据库行锁完成唯一终态汇总，报告、设备释放和队列结束在同一事务中完成。创建执行快照时预建 pending 步骤，中断后可正确标记 skipped。
- 修复 queued 取消与 Worker 认领竞争，只有成功更新执行状态的一方才能改变队列。
- 修复新建用例路由的 `NaN` 问题、元素可选字段无法清空和用例嵌套参数校验不严格的问题。
- 补齐历史唯一键数据清理和已有 admin 用户提升迁移；种子脚本遇到已有 admin 时更新管理员标记。
- 后端测试切换到独立 `test_platform_test` 数据库，并拒绝在非测试库运行；测试创建的用户、Agent、设备和执行必须完整清理。
- CI 增加前端测试步骤，所有现有阻断项回归通过后进入 Agent 功能开发。

## 3. 数据模型与服务端接口

### 3.1 数据模型

新增：

- `user_agent_keys`：每用户一条专属 Key，包含公开标识、Argon2 哈希、Fernet 密文和更新时间。Key 格式为 `uak_<public_id>_<secret>`，前端可长期查看。
- `agent_users`：Agent 与用户多对多绑定，包含 `agent_id`、`user_id`、绑定撤销凭据哈希和时间戳，组合唯一。
- `device_preferences`：每用户一个默认设备，设备删除时自动清空。
- `devices.connection_type`：`usb` 或 `wifi`。
- `devices.address`：无线连接地址，可为空。
- `executions.stop_requested_at`、`executions.finalized_at`。

已有 Agent 在迁移后不自动绑定普通用户，平台管理员仍可查看和使用；用户随后可在本地 Agent 中录入 Key 建立绑定。

### 3.2 Key 与绑定接口

- `GET /api/me/agent-key`：解密并返回当前用户 Key；不存在时返回空状态。
- `POST /api/me/agent-key`：首次生成 Key。
- `POST /api/me/agent-key/regenerate`：生成新 Key；旧 Key 不能再新增绑定，但已有 Agent 绑定继续有效。
- `POST /api/agent/bind`：
  - 第一次绑定提交用户 Key、`install_id`、主机名、版本和平台，创建 Agent、机器 PSK 和 `agent_users`。
  - 后续绑定同时提交机器 PSK 和另一个用户 Key，只新增关联。
  - 返回 Agent 身份和该用户绑定的撤销凭据；原始用户 Key 不写日志、不保存在 Agent 配置文件。
- `GET /api/agent/bindings`：机器 PSK 认证，返回本机已绑定用户名。
- `DELETE /api/agent/bindings/{id}`：机器 PSK + 对应撤销凭据解绑。
- `DELETE /api/agents/{id}/bindings/me`：用户从前端撤销自己对该 Agent 的授权。
- 绑定接口按 IP、Key 公开标识限流；机器 PSK、绑定撤销凭据保存到 Windows Credential Manager。
- 新增生产必填配置 `AGENT_USER_KEY_ENCRYPTION_KEY`，用于用户 Key 可逆加密；数据库不裸存明文。

### 3.3 设备与执行接口

- 普通用户的 `/api/agents`、`/api/devices`、详情接口只返回其绑定 Agent 下的资源；平台管理员返回全部。
- 所有设备详情、默认设备设置、执行创建和重试均再次校验 Agent↔用户授权，不能通过猜测 ID 使用他人设备。
- `GET /api/devices/default`：返回当前默认设备及实时可用性。
- `PUT /api/devices/default`：设置或清除默认设备；只能设置用户有权限的设备。
- 执行创建请求中的 `device_id` 改为必填；缺失返回明确的 `DEVICE_REQUIRED` 错误，Worker 不再从全平台设备池随机选择。
- 指定设备必须同时满足：用户有权使用、Agent 在线、设备 idle、未被其他执行锁定。最终仍由 Worker 原子锁保证并发安全。
- Agent 全量设备快照更新时，已被执行锁定的设备必须保持 busy，不允许普通设备上报覆盖锁状态。

### 3.4 安装包下载接口

- 配置 `AGENT_RELEASES_PATH`，目录内保存版本化安装包和 `latest.json`：`version`、`filename`、`sha256`、`size`、`published_at`。
- `GET /api/agent/releases/latest`：登录用户获取最新版元数据。
- `POST /api/agent/releases/latest/download-token`：生成 5 分钟有效、限定文件名的下载 JWT。
- `GET /api/agent/releases/download/{filename}?token=...`：校验令牌和安全文件名后使用 `FileResponse` 流式下载，支持 Range 和正确的 `Content-Disposition`。
- 不把大型安装包打进前端 Bundle 或 Git 仓库；由发布脚本复制到后端发布目录并原子更新 manifest。

## 4. Windows Agent 与前端交互

### 4.1 本地 Agent

- 将现有 Agent 拆分为连接管理、设备注册表、ADB 管理、Appium 生命周期、执行器和桌面控制器；托盘与窗口使用 Tkinter + pystray，asyncio 网络循环运行在独立后台线程。
- 本地窗口包含：
  - 服务器、Agent ID、版本、在线状态和 Appium 状态。
  - 已绑定用户列表、用户 Key 输入框、绑定和解绑操作。
  - 本地设备表：名称、序列号/地址、Android 版本、USB/Wi-Fi、授权状态和当前状态。
  - “连接无线设备”窗口：设备地址、连接端口；可选配对地址、配对端口和配对码。
  - 手动刷新、无线断开、打开日志目录和退出 Agent。
- `adb devices -l` 每 3 秒扫描一次；设备集合变化时立即发送 `device_list`，无变化时每 30 秒发送一次完整快照。
- `adb pair`、`adb connect`、`adb disconnect` 使用参数数组调用且禁用 shell，校验主机/IP 和端口，15 秒超时，并把 ADB 错误翻译为可读提示。
- `unauthorized` 显示“请在设备上允许 USB 调试”，`offline` 显示连接异常；只有 `device` 状态上报为 idle。
- Appium 在需要执行时由 Agent 隐藏启动，监听 `127.0.0.1`，执行结束或 Agent 退出时清理 Session 和子进程。
- 程序数据、日志和临时截图放在 LocalAppData；程序文件只读安装，日志按大小轮转。

### 4.2 前端

- 设备页改为“Agent 下载与 Key / 已绑定 Agent / 我的设备”三个区域：
  - 查看、复制或重置个人专属 Key。
  - 下载 Windows x64 安装包，显示版本、大小和 SHA-256。
  - 查看绑定 Agent、在线状态、版本和设备；用户可撤销自己的绑定，管理员可管理全部。
  - 设备可设为默认，默认设备显示明显标记。
- 点击运行用例、套件或批量套件时：
  1. 查询默认设备。
  2. 默认设备有权限、Agent 在线且设备空闲时，直接创建执行并跳转执行详情。
  3. 未设置默认设备、默认设备忙碌/离线/失权时，弹出选择窗口。
  4. 选择窗口只显示当前用户有权限且在线空闲的设备，并提供“设为默认设备”选项。
  5. 没有可用设备时显示下载安装 Agent、绑定 Key 和连接设备的引导，不允许提交空 `device_id`。
- 默认设备发生并发占用时，显示“设备刚被其他执行占用”，刷新选择列表，不自动改用其他设备。

## 5. 构建、测试与验收

- 增加 Windows 构建配置：PyInstaller `onedir` 生成 Agent 目录，Inno Setup 生成版本化 `app-auto-test-agent-<version>-windows-x64-setup.exe`。
- 安装器创建开始菜单和卸载项、注册当前用户登录自启动；安装完成后启动托盘 Agent。打包后的 EXE 无参数启动（包括双击和开始菜单）默认打开桌面管理窗口，源码运行仍默认使用无头模式；开始菜单、自启动和安装完成启动项同时显式传入 `--desktop`。默认不需要管理员权限。
- Windows CI 执行 Agent 测试、`--self-check`、PyInstaller、安装器编译和 SHA-256 manifest 生成。若配置证书则执行 Authenticode 签名；当前内网版本允许无证书发布，但保留 Windows SmartScreen 提示说明。
- 后端覆盖：Key 生成/查看/重置、首次和追加绑定、解绑、管理员权限、越权设备访问、默认设备、下载令牌、路径穿越、设备锁竞争、停止宽限期和唯一终态汇总。
- Agent 覆盖：ADB 输出解析、USB 热插拔、无线配对/连接失败、设备快照、机器凭据恢复、多用户绑定、Appium 阻塞停止、托盘控制器和打包资源定位。
- 前端覆盖：Key 和下载流程、设备权限过滤、默认设备直接运行、默认不可用时弹窗、选择后设默认、并发占用错误。

完整验收场景：

1. 两个用户 Key 绑定同一 Agent，双方和管理员可见，第三个用户不可见。
2. 一台电脑同时连接多台 USB/Wi-Fi Android 设备，前端在 5 秒内同步。
3. 两个用户同时抢同一设备，仅一个执行获得锁。
4. 默认设备空闲时直接执行；忙碌、离线或未设置时弹出选择窗口。
5. 安装、登录自启动、卸载、后端下载安装、真实 Android 建连、执行、停止、截图上传和报告生成全链路通过。

每个实施阶段分别执行 Backend/Agent Ruff、测试、前端测试与构建、Alembic upgrade/check，并按项目提交规范独立提交。

## 6. 实施阶段

### Step 1：权威设计与前置整改

- 更新权威架构文档第 10 章。
- 完成执行、停止、终态汇总、截图上传、迁移和测试隔离整改。
- 全量测试通过后提交一次。

### Step 2：多用户绑定与设备权限

- 新增数据表、迁移、Key 加密和绑定接口。
- 改造 Agent/设备查询权限和默认设备接口。
- 增加跨用户权限矩阵测试并提交。

### Step 3：本地设备管理与 Agent 核心

- 实现 ADB 设备注册表、USB/Wi-Fi 发现、配对、连接和断开。
- 实现机器凭据、本地绑定管理、Appium 生命周期和非阻塞执行。
- 完成 Agent 单元测试和 mock 联调后提交。

### Step 4：托盘界面与 Windows 安装包

- 实现本地托盘、设备窗口、绑定窗口和日志管理。
- 增加 PyInstaller、Inno Setup、`--self-check` 和 Windows CI。
- 生成可安装、可卸载、可自启动的安装包后提交。

### Step 5：后端分发与前端自动选机

- 实现安装包 manifest、下载令牌和流式下载。
- 重构设备页和运行入口，完成默认设备直接运行和选择弹窗。
- 前端测试、构建和端到端联调通过后提交。

### Step 6：完整验收

- 使用独立测试库执行后端全量测试。
- 执行 Agent、前端、Alembic 和 Windows 安装包验证。
- 使用至少一台真实 Android 设备完成 USB/Wi-Fi、运行、停止、截图和报告全链路验收。

## 7. 假设与边界

- 第一版只交付 Windows x64 和 Android；iOS/macOS Agent 后续单独规划。
- 用户 Key 按要求可长期查看，但使用服务端主密钥加密存储；重置不影响已有绑定。
- Agent 可以绑定多个用户，所有绑定用户权限相同；平台管理员始终可查看和使用全部 Agent/设备。
- 默认设备是用户级全局设置，不按项目区分。
- 安装包直接由后端服务器托管，用户量较小，不引入 CDN 和自动更新系统。
