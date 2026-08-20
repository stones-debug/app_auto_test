<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage, ElMessageBox } from 'element-plus'

import {
  EXECUTION_STATUS,
  executionStatusMeta,
  getExecution,
  getExecutionLogs,
  listExecutions,
  retryExecution,
  stopExecution,
  type ExecutionDetail,
  type ExecutionListItem,
  type ExecutionLog,
  type ExecutionStatus,
} from '@/api/executions'
import { useExecutionSocket } from '@/composables/useExecutionSocket'
import { getToken } from '@/utils/request'

const route = useRoute()

const loading = ref(false)
const items = ref<ExecutionListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const statusFilter = ref('')
const typeFilter = ref('')

const drawerOpen = ref(false)
const detail = ref<ExecutionDetail | null>(null)
const logs = ref<ExecutionLog[]>([])
const logsLoading = ref(false)
const lastTimestamp = ref<string | null>(null)
let socket: { connected: { value: boolean }; close: () => void } | null = null

async function load() {
  loading.value = true
  try {
    const data = await listExecutions({
      page: page.value,
      page_size: pageSize.value,
      status: statusFilter.value,
      type: typeFilter.value,
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function typeLabel(t: string) {
  return { case: '用例', suite: '套件', batch: '批量' }[t] ?? t
}

function durationText(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function levelType(level: string) {
  if (level === 'ERROR') return 'danger'
  if (level === 'WARN') return 'warning'
  if (level === 'DEBUG') return 'info'
  return 'primary'
}

function isTerminal(status: string) {
  return ['passed', 'failed', 'error', 'stopped', 'cancelled'].includes(status)
}

function subscribe() {
  socket?.close()
  if (!detail.value || isTerminal(detail.value.status)) return
  const token = getToken() ?? ''
  socket = useExecutionSocket(detail.value.id, token, (msg) => {
    const type = msg.type as string
    if (type === 'status' && detail.value) {
      detail.value.status = msg.status as ExecutionStatus
    } else if (type === 'log' && detail.value && msg.execution_id === detail.value.id) {
      const item: ExecutionLog = {
        id: Date.now(),
        execution_id: detail.value.id,
        level: (msg.level as string) ?? 'INFO',
        message: (msg.message as string) ?? '',
        source: 'live',
        created_at: (msg.timestamp as string) ?? new Date().toISOString(),
      }
      logs.value.push(item)
      scrollLogs()
    } else if (type === 'step_result' && detail.value) {
      const caseId = Number(msg.case_id)
      const ec = detail.value.cases.find((c) => c.case_id === caseId)
      if (ec) ec.status = (msg.status as string) ?? ec.status
    } else if (type === 'completed' && detail.value) {
      detail.value.status = (msg.status as ExecutionStatus) ?? detail.value.status
      socket?.close()
      load()
    }
  })
}

const logBody = ref<HTMLElement | null>(null)
function scrollLogs() {
  nextTick(() => {
    if (logBody.value) logBody.value.scrollTop = logBody.value.scrollHeight
  })
}

async function openDetail(id: number) {
  drawerOpen.value = true
  detail.value = null
  logs.value = []
  lastTimestamp.value = null
  const data = await getExecution(id)
  detail.value = data
  subscribe()
  await loadLogs()
}

async function loadLogs() {
  if (!detail.value) return
  logsLoading.value = true
  try {
    const data = await getExecutionLogs(detail.value.id, { page_size: 200 })
    logs.value = data.items
    lastTimestamp.value = data.items.length ? data.items[data.items.length - 1].created_at : null
    scrollLogs()
  } finally {
    logsLoading.value = false
  }
}

function closeDrawer() {
  socket?.close()
  socket = null
  drawerOpen.value = false
  detail.value = null
}

async function stop(id: number) {
  await ElMessageBox.confirm('确认停止该执行？', '提示', { type: 'warning' })
  await stopExecution(id)
  ElMessage.success('已请求停止')
  await load()
  if (detail.value?.id === id) {
    detail.value.status = 'stopping'
    subscribe()
  }
}

async function retry(id: number) {
  const exec = await retryExecution(id)
  ElMessage.success(`已创建重试执行 #${exec.id}`)
  await load()
  await openDetail(exec.id)
}

function onSearch() {
  page.value = 1
  load()
}

onMounted(() => {
  load()
  const focus = Number(route.query.focus)
  if (focus) openDetail(focus)
})

onBeforeUnmount(() => {
  socket?.close()
})
</script>

<template>
  <div>
    <div class="toolbar">
      <el-select v-model="statusFilter" placeholder="状态" clearable class="w160" @change="onSearch">
        <el-option v-for="s in EXECUTION_STATUS" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-select v-model="typeFilter" placeholder="类型" clearable class="w120" @change="onSearch">
        <el-option label="用例" value="case" />
        <el-option label="套件" value="suite" />
        <el-option label="批量" value="batch" />
      </el-select>
      <el-button type="primary" @click="onSearch">搜索</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="items">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ typeLabel(row.type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="名称" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">{{ row.case_name ?? row.suite_name ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="executionStatusMeta(row.status).type" size="small">{{ executionStatusMeta(row.status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="device_id" label="设备" width="80" />
      <el-table-column label="耗时" width="100">
        <template #default="{ row }">{{ durationText(row.duration) }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" text @click="openDetail(row.id)">详情</el-button>
          <el-button size="small" text @click="stop(row.id)">停止</el-button>
          <el-button size="small" text @click="retry(row.id)">重试</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      class="pager"
      @change="load"
    />

    <el-drawer v-model="drawerOpen" size="60%" :destroy-on-close="false" @closed="closeDrawer">
      <template #header>
        <div class="drawer-header">
          <span>执行 #{{ detail?.id }}</span>
          <el-tag v-if="detail" :type="executionStatusMeta(detail.status).type" size="small">
            {{ executionStatusMeta(detail.status).label }}
          </el-tag>
          <div class="drawer-actions">
            <el-button size="small" type="warning" @click="detail && stop(detail.id)">停止</el-button>
            <el-button size="small" type="primary" @click="detail && retry(detail.id)">重试</el-button>
          </div>
        </div>
      </template>

      <template v-if="detail">
        <el-descriptions :column="3" border size="small" class="mb16">
          <el-descriptions-item label="类型">{{ typeLabel(detail.type) }}</el-descriptions-item>
          <el-descriptions-item label="设备">#{{ detail.device_id ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="超时">{{ detail.timeout_seconds }}s</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ detail.started_at ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ detail.finished_at ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ durationText(detail.duration) }}</el-descriptions-item>
        </el-descriptions>

        <div class="section-title">用例</div>
        <el-table :data="detail.cases" size="small" class="mb16">
          <el-table-column prop="case_name" label="用例" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="executionStatusMeta(row.status).type" size="small">{{ executionStatusMeta(row.status).label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="90">
            <template #default="{ row }">{{ durationText(row.duration) }}</template>
          </el-table-column>
          <el-table-column prop="error_message" label="错误" min-width="180" show-overflow-tooltip />
        </el-table>

        <div class="section-title">执行日志</div>
        <div ref="logBody" v-loading="logsLoading" class="log-box">
          <el-table :data="logs" size="small">
            <el-table-column prop="level" label="级别" width="80">
              <template #default="{ row }">
                <el-tag :type="levelType(row.level)" size="small">{{ row.level }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="message" label="消息" min-width="300" show-overflow-tooltip />
            <el-table-column prop="source" label="来源" width="90" />
            <el-table-column prop="created_at" label="时间" width="170" />
          </el-table>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.w160 {
  width: 160px;
}
.w120 {
  width: 120px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.drawer-header {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}
.drawer-actions {
  margin-left: auto;
}
.section-title {
  font-weight: 600;
  margin-bottom: 8px;
}
.mb16 {
  margin-bottom: 16px;
}
.log-box {
  max-height: 360px;
  overflow-y: auto;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
</style>
