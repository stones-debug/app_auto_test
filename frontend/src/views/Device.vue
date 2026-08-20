<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { ElMessage, ElMessageBox } from 'element-plus'

import {
  agentStatusType,
  agentDevices,
  createAgent,
  deleteAgent,
  deviceStatusType,
  listAgents,
  releaseDevice,
  type Agent,
  type Device,
} from '@/api/agents'

const loading = ref(false)
const agents = ref<Agent[]>([])
const devicesByAgent = ref<Record<number, Device[]>>({})
let timer: ReturnType<typeof setInterval> | null = null

const dialogOpen = ref(false)
const form = ref({ hostname: '', platform: 'windows' })

async function load() {
  loading.value = true
  try {
    agents.value = await listAgents()
    for (const agent of agents.value) {
      devicesByAgent.value[agent.id] = await agentDevices(agent.id)
    }
  } finally {
    loading.value = false
  }
}

async function submitCreate() {
  const agent = await createAgent({
    hostname: form.value.hostname || undefined,
    platform: form.value.platform || undefined,
  })
  dialogOpen.value = false
  form.value = { hostname: '', platform: 'windows' }
  ElMessageBox.alert(
    `agent_id: ${agent.agent_id}\nagent_key: ${agent.agent_key}\n\n请复制 agent_key 到 Agent 的 config.yaml。`,
    'Agent 已创建',
    { confirmButtonText: '我已复制' },
  )
  await load()
}

async function remove(agent: Agent) {
  await ElMessageBox.confirm(`确认注销 Agent「${agent.agent_id}」及其所有设备？`, '提示', { type: 'warning' })
  await deleteAgent(agent.id)
  ElMessage.success('已注销')
  await load()
}

async function release(device: Device) {
  await ElMessageBox.confirm(`确认强制释放设备「${device.name}」的锁？`, '提示', { type: 'warning' })
  await releaseDevice(device.id)
  ElMessage.success('已释放')
  await load()
}

function fmtTime(t: string | null) {
  return t ? new Date(t).toLocaleString() : '-'
}

onMounted(() => {
  load()
  timer = setInterval(load, 5000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="dialogOpen = true">新建 Agent</el-button>
      <el-button @click="load">刷新</el-button>
      <span class="tip">状态每 5 秒自动刷新</span>
    </div>

    <el-table v-loading="loading" :data="agents" row-key="id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand-wrap">
            <el-table :data="devicesByAgent[row.id] ?? []" size="small">
              <el-table-column prop="name" label="设备" min-width="160" />
              <el-table-column prop="udid" label="UDID" min-width="140" />
              <el-table-column prop="platform" label="平台" width="90" />
              <el-table-column prop="device_type" label="类型" width="90" />
              <el-table-column label="状态" width="90">
                <template #default="{ row: d }">
                  <el-tag :type="deviceStatusType(d.status)" size="small">{{ d.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="执行锁" width="90">
                <template #default="{ row: d }">#{{ d.locked_by_execution ?? '-' }}</template>
              </el-table-column>
              <el-table-column label="操作" width="120">
                <template #default="{ row: d }">
                  <el-button size="small" type="warning" text @click="release(d)">释放锁</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="agent_id" label="Agent ID" min-width="160" />
      <el-table-column prop="hostname" label="主机名" min-width="120" />
      <el-table-column prop="platform" label="平台" width="90" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="agentStatusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="version" label="版本" width="80" />
      <el-table-column label="最后心跳" min-width="170">
        <template #default="{ row }">{{ fmtTime(row.last_heartbeat) }}</template>
      </el-table-column>
      <el-table-column prop="device_count" label="设备数" width="80" />
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="danger" text @click="remove(row)">注销</el-button>
        </template>
      </el-table-column>
    </el-table>

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
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
}
.tip {
  color: #999;
  font-size: 12px;
  margin-left: 8px;
}
.expand-wrap {
  padding: 8px 24px;
}
</style>
