<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'


import {
  agentDevices,
  agentStatusType,
  createAgent,
  deleteAgent,
  deviceStatusType,
  getDefaultDevice,
  listAgents,
  releaseDevice,
  setDefaultDevice,
  unbindMyAgent,
  type Agent,
  type DefaultDevice,
  type Device,
} from '@/api/agents'
import { createMyAgentKey, getMyAgentKey, regenerateMyAgentKey, type AgentKey } from '@/api/me'
import { getDownloadToken, getLatestRelease, releaseDownloadUrl, type ReleaseManifest } from '@/api/releases'
import { useAuthStore } from '@/stores/auth'
import { copyText } from '@/utils/clipboard'
import { formatDateTime } from '@/utils/format'
import { apiErrorDetail } from '@/utils/request'

const auth = useAuthStore()
const isAdmin = ref(auth.user?.is_admin ?? false)

const loading = ref(false)
const agents = ref<Agent[]>([])
const devicesByAgent = ref<Record<number, Device[]>>({})
const defaultDevice = ref<DefaultDevice>({ device_id: null, device: null, available: false, reason: '' })
let timer: ReturnType<typeof setInterval> | null = null

// ---------- 区域一：个人 Key + Agent 下载 ----------

const myKey = ref<AgentKey | null>(null)
const releaseInfo = ref<ReleaseManifest | null>(null)
const downloading = ref(false)

async function loadKey() {
  myKey.value = await getMyAgentKey()
}

async function createKey() {
  await createMyAgentKey()
  await loadKey()
  ElMessage.success('已生成个人 Key')
}

async function resetKey() {
  await ElMessageBox.confirm(
    '重置后旧 Key 不能再新增绑定（已有 Agent 绑定继续有效）。确认重置？',
    '重置 Key',
    { type: 'warning' },
  )
  await regenerateMyAgentKey()
  await loadKey()
  ElMessage.success('Key 已重置')
}

async function copyKey() {
  const key = myKey.value?.key
  if (!key) return

  try {
    await copyText(key)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动复制')
  }
}

async function loadRelease() {
  try {
    releaseInfo.value = await getLatestRelease()
  } catch {
    releaseInfo.value = null
  }
}

async function downloadRelease() {
  downloading.value = true
  try {
    const { token, filename } = await getDownloadToken()
    window.open(releaseDownloadUrl(filename, token), '_blank')
  } finally {
    downloading.value = false
  }
}

function fmtSize(size: number) {
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024).toFixed(1)} KB`
}

// ---------- 区域二/三：绑定 Agent 与设备 ----------

async function load() {
  loading.value = true
  try {
    agents.value = await listAgents()
    devicesByAgent.value = {}
    for (const agent of agents.value) {
      devicesByAgent.value[agent.id] = await agentDevices(agent.id)
    }
    defaultDevice.value = await getDefaultDevice()
  } finally {
    loading.value = false
  }
}

async function revoke(agent: Agent) {
  await ElMessageBox.confirm(`确认撤销你对 Agent「${agent.agent_id}」的授权？`, '提示', { type: 'warning' })
  await unbindMyAgent(agent.id)
  ElMessage.success('已撤销绑定')
  await load()
}

async function setDefault(device: Device) {
  await setDefaultDevice(device.id)
  ElMessage.success(`已将「${device.name}」设为默认设备`)
  await load()
}

async function clearDefault() {
  await setDefaultDevice(null)
  ElMessage.success('已清除默认设备')
  await load()
}

function allDevices(): Device[] {
  return Object.values(devicesByAgent.value).flat()
}

function fmtTime(t: string | null) {
  return t ? formatDateTime(t) : '-'
}

const dialogOpen = ref(false)
const form = ref({ hostname: '', platform: 'windows' })

async function submitCreate() {
  const agent = await createAgent({
    hostname: form.value.hostname || undefined,
    platform: form.value.platform || undefined,
  })
  dialogOpen.value = false
  form.value = { hostname: '', platform: 'windows' }
  ElMessageBox.alert(
    `agent_id: ${agent.agent_id}\nagent_key: ${agent.agent_key}\n\n旧版手工配置可使用 agent_key。\n新版桌面 Agent 请到“个人中心”生成以 uak_ 开头的用户 Key，再在 Agent 界面绑定。`,
    'Agent 已创建',
    { confirmButtonText: '我已复制' },
  )
  await load()
}

async function remove(agent: Agent) {
  await ElMessageBox.confirm(`确认注销 Agent「${agent.agent_id}」？（设备与历史执行/报告保留）`, '提示', { type: 'warning' })
  try {
    await deleteAgent(agent.id)
    ElMessage.success('已注销')
  } catch (error) {
    // Step 6：活动执行时返回 409 AGENT_HAS_ACTIVE_EXECUTIONS，展示具体执行信息
    const detail = apiErrorDetail(error)
    if (detail?.code === 'AGENT_HAS_ACTIVE_EXECUTIONS' && detail.message) {
      ElMessage.warning(detail.message)
      return
    }
    throw error
  }
  await load()
}

async function release(device: Device) {
  await ElMessageBox.confirm(`确认强制释放设备「${device.name}」的锁？`, '提示', { type: 'warning' })
  // Step 6：活动执行只请求停止、等待 Worker 汇总，不立即清锁
  const resp = await releaseDevice(device.id)
  if (resp.action === 'released') {
    ElMessage.success('设备已释放')
  } else if (resp.action === 'stop_requested') {
    ElMessage.warning(`已请求停止执行 #${resp.execution_id}，设备保持占用直至 Worker 汇总`)
  } else {
    ElMessage.info(`执行 #${resp.execution_id} 已结束，等待 Worker 汇总后自动释放`)
  }
  await load()
}

onMounted(() => {
  loadKey()
  loadRelease()
  load()
  timer = setInterval(load, 5000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div>
    <!-- 区域一：Agent 下载与 Key -->
    <el-card shadow="never" class="section">
      <template #header>
        <span>Agent 下载与个人 Key</span>
      </template>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="Windows Agent">
          <template v-if="releaseInfo">
            <el-button type="primary" size="small" :loading="downloading" @click="downloadRelease">
              下载 v{{ releaseInfo.version }}
            </el-button>
            <span class="muted"> {{ fmtSize(releaseInfo.size) }} · SHA-256: {{ releaseInfo.sha256.slice(0, 16) }}…</span>
          </template>
          <span v-else class="muted">暂无发布版本（管理员发布后显示）</span>
        </el-descriptions-item>
        <el-descriptions-item label="我的 Key">
          <template v-if="myKey?.exists && myKey.key">
            <code class="key-text">{{ myKey.key }}</code>
            <el-button size="small" text type="primary" @click="copyKey">复制</el-button>
            <el-button size="small" text type="warning" @click="resetKey">重置</el-button>
          </template>
          <template v-else>
            <span class="muted">尚未生成 Key</span>
            <el-button size="small" type="primary" @click="createKey">生成 Key</el-button>
          </template>
        </el-descriptions-item>
      </el-descriptions>
      <div class="hint">安装 Agent 后，把上面的 Key 粘贴到 Agent 的“绑定 Key”输入框完成绑定。</div>
    </el-card>

    <!-- 区域二：已绑定 Agent -->
    <el-card shadow="never" class="section">
      <template #header>
        <div class="card-head">
          <span>已绑定 Agent</span>
          <div>
            <el-button v-if="isAdmin" size="small" type="primary" @click="dialogOpen = true">新建 Agent</el-button>
            <el-button size="small" @click="load">刷新</el-button>
            <span class="muted">状态每 5 秒自动刷新</span>
          </div>
        </div>
      </template>
      <el-table v-loading="loading" :data="agents" row-key="id" size="small">
        <el-table-column prop="agent_id" label="Agent ID" min-width="160" />
        <el-table-column prop="hostname" label="主机名" min-width="110" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="agentStatusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column prop="device_count" label="设备数" width="80" />
        <el-table-column label="最后心跳" min-width="160">
          <template #default="{ row }">{{ fmtTime(row.last_heartbeat) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="danger" text @click="revoke(row as Agent)">撤销绑定</el-button>
            <el-button v-if="isAdmin" size="small" type="danger" text @click="remove(row as Agent)">注销</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 区域三：我的设备 -->
    <el-card shadow="never" class="section">
      <template #header>
        <div class="card-head">
          <span>我的设备</span>
          <el-button v-if="defaultDevice.device_id" size="small" @click="clearDefault">清除默认设备</el-button>
        </div>
      </template>
      <el-table :data="allDevices()" row-key="id" size="small">
        <el-table-column label="默认" width="70">
          <template #default="{ row }">
            <el-tag v-if="defaultDevice.device_id === (row as Device).id" type="warning" size="small">★ 默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="设备" min-width="150" />
        <el-table-column prop="udid" label="序列号/地址" min-width="150" />
        <el-table-column prop="address" label="无线地址" min-width="130" />
        <el-table-column label="连接" width="80">
          <template #default="{ row }">{{ (row as Device).connection_type === 'wifi' ? 'Wi-Fi' : 'USB' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="deviceStatusType((row as Device).status)" size="small">{{ (row as Device).status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="所属 Agent" min-width="120">
          <template #default="{ row }">{{ (row as Device).agent_name ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              text
              :disabled="defaultDevice.device_id === (row as Device).id"
              @click="setDefault(row as Device)"
            >
              设为默认
            </el-button>
            <el-button v-if="isAdmin" size="small" type="warning" text @click="release(row as Device)">释放锁</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!allDevices().length" class="muted empty-tip">
        暂无可用设备。安装 Agent 并绑定 Key、连接 USB / 无线设备后，这里会实时显示。
      </div>
    </el-card>

    <el-dialog v-model="dialogOpen" title="新建 Agent" width="420px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="主机名">
          <el-input v-model="form.hostname" placeholder="例如 test-pc-01" />
        </el-form-item>
        <el-form-item label="平台">
          <el-select v-model="form.platform">
            <el-option label="Windows" value="windows" />
            <el-option label="macOS" value="macos" />
            <el-option label="Linux" value="linux" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.section {
  margin-bottom: 16px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.muted {
  color: #999;
  font-size: 12px;
  margin-left: 8px;
}
.key-text {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.hint {
  color: #909399;
  font-size: 12px;
  margin-top: 8px;
}
.empty-tip {
  text-align: center;
  padding: 24px 0;
}
</style>
