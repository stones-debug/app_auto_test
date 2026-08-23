<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getExecution,
  getExecutionLogs,
  stopExecution,
  type ExecutionDetail,
  type ExecutionLog,
  type ExecutionStatus,
} from '@/api/executions'
import { findReportByExecution } from '@/api/reports'
import DevicePicker from '@/components/DevicePicker.vue'
import ExecutionParameters from '@/components/ExecutionParameters.vue'
import ExecutionTimeline, { type TimelineCase } from '@/components/ExecutionTimeline.vue'
import LiveLogViewer, { type LogEntry } from '@/components/LiveLogViewer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useExecutionRetry } from '@/composables/useExecutionRetry'
import { useExecutionSocket } from '@/composables/useExecutionSocket'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'
import { getToken } from '@/utils/request'
import { liveLogKey, mergeExecutionLogs, type LogLike } from '@/utils/executionLogs'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const navigation = useWorkspaceNavigation()
// Step 7：executionId 改 computed，路由复用/参数变化时重载
const executionId = computed(() => Number(route.params.executionId))

const loading = ref(false)
const detail = ref<ExecutionDetail | null>(null)
const stopping = ref(false)
const reportId = ref<number | null>(null)

// REST 日志（后端 id 去重）与 WS live 日志（单调递减临时 id，避免 Date.now 碰撞）
const logs = ref<ExecutionLog[]>([])
const liveLogs = ref<LogEntry[]>([])
let liveIdCounter = 0
let logKeys = new Set<string>()
let completedPulled = false

let socket: ReturnType<typeof useExecutionSocket> | null = null
let staleId = 0 // loadAll 的异步完成检查：只允许当前路由的请求生效

const timelineCases = computed<TimelineCase[]>(() => {
  return (detail.value?.cases ?? []).map((c) => ({
    case_id: c.case_id,
    case_name: c.case_name,
    status: c.status,
    steps: (c.steps ?? []).map((s) => ({
      step_order: s.step_order,
      action: s.action,
      parameters: s.parameters,
      status: s.status,
      duration: s.duration,
      actual_value: s.actual_value,
      error_message: s.error_message,
      // Step 7：保留 API 返回的 artifact_id（截图鉴权入口依赖）
      artifact_id: s.artifact_id ?? null,
    })),
    assertions: (c.assertions ?? []).map((a) => ({
      assertion_type: a.assertion_type,
      expected_value: a.expected_value,
      actual_value: a.actual_value,
      status: a.status,
      error_message: a.error_message,
    })),
  }))
})

const logEntries = computed<LogEntry[]>(() => {
  const live: LogLike[] = liveLogs.value.map((l) => ({ id: l.id, level: l.level, message: l.message, source: l.source, created_at: l.created_at }))
  return mergeExecutionLogs(logs.value, live) as LogEntry[]
})

function isTerminal(status: string) {
  return ['passed', 'failed', 'error', 'stopped', 'cancelled'].includes(status)
}

function resetLogs() {
  logs.value = []
  liveLogs.value = []
  liveIdCounter = 0
  logKeys = new Set()
}

async function loadAll(id: number) {
  const myStale = ++staleId
  loading.value = true
  try {
    const data = await getExecution(id)
    if (staleId !== myStale) return // 旧请求晚到，不覆盖当前路由数据
    await navigation.normalizeProjectDetail('execution', id, data.project_id)
    if (staleId !== myStale) return
    detail.value = data
    resetLogs()
    const logData = await getExecutionLogs(id, { page_size: 200 })
    if (staleId !== myStale) return
    logs.value = logData.items
    reportId.value = null
    completedPulled = false
    if (isTerminal(data.status)) {
      socket?.close()
      const rid = await findReportByExecution(id)
      if (staleId === myStale) reportId.value = rid
      return
    }
    subscribe(id)
  } finally {
    loading.value = false
  }
}

function subscribe(id: number) {
  const token = getToken() ?? ''
  // Step 7：subscribe 前强制关闭旧 socket（防止新老执行串流）
  socket?.close()
  const ws = useExecutionSocket(id, token, (msg) => {
    const type = msg.type as string
    if (type === 'status') {
      if (detail.value) detail.value.status = (msg.status as ExecutionStatus) ?? detail.value.status
    } else if (type === 'log' && msg.execution_id === id) {
      const key = liveLogKey(String(msg.level ?? ''), String(msg.message ?? ''), String(msg.timestamp ?? ''))
      if (logKeys.has(key)) return
      logKeys.add(key)
      liveLogs.value.push({
        id: --liveIdCounter, // 单调递减临时 id，避免 Date.now 同毫秒碰撞
        level: (msg.level as string) ?? 'INFO',
        message: (msg.message as string) ?? '',
        source: 'live',
        created_at: (msg.timestamp as string) ?? new Date().toISOString(),
      })
    } else if (type === 'step_result') {
      updateCaseStatus(Number(msg.case_id), (msg.status as string) ?? 'running')
    } else if (type === 'completed') {
      // Step 7：completed 只触发一次 REST 补拉并关闭 socket
      if (completedPulled) return
      completedPulled = true
      if (detail.value) detail.value.status = (msg.status as ExecutionStatus) ?? detail.value.status
      ws.close()
      void loadAll(id)
    }
  })
  socket = ws
}

function updateCaseStatus(caseId: number, status: string) {
  const ec = detail.value?.cases.find((c) => c.case_id === caseId)
  if (ec) ec.status = status
}

const { picker, retry: retryEntry, running: retrying } = useExecutionRetry()

async function stop() {
  stopping.value = true
  try {
    await stopExecution(executionId.value)
    ElMessage.success('已请求停止')
    if (detail.value) detail.value.status = 'stopping'
  } finally {
    stopping.value = false
  }
}

async function retry() {
  // Step 5：重试统一走 DevicePicker，成功后跳新 execution 详情
  await retryEntry(executionId.value, `执行 #${executionId.value}`)
}

function viewReport() {
  if (reportId.value != null) void router.push(navigation.reportDetail(reportId.value))
}

function durationText(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

// Step 7：watch executionId——先关旧 socket，再清空 detail/logs/reportId，最后加载新 ID
watch(
  executionId,
  (id, oldId) => {
    if (id === oldId) return
    socket?.close()
    socket = null
    detail.value = null
    logs.value = []
    liveLogs.value = []
    reportId.value = null
    completedPulled = false
    if (Number.isFinite(id)) void loadAll(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => socket?.close())
</script>

<template>
  <div v-loading="loading">
    <div class="head-bar">
      <div>
        <el-button size="small" text @click="navigation.back('execution')">← 返回</el-button>
        <div class="head-title">
          <span class="v2-page-title">执行 #{{ executionId }}</span>
          <StatusBadge v-if="detail" :status="detail.status" />
        </div>
        <div v-if="detail" class="head-meta v2-aux">
          项目 {{ detail.project_name ?? '-' }} · 设备 {{ detail.device_name ?? '-' }} ·
          创建人 {{ detail.created_by_name ?? '-' }}
        </div>
      </div>
      <div class="head-actions">
        <el-button v-if="['queued', 'running', 'stopping'].includes(detail?.status ?? '')" type="warning" :loading="stopping" @click="stop">停止</el-button>
        <el-button type="primary" :loading="retrying" @click="retry">重试</el-button>
        <el-button v-if="reportId != null" type="success" @click="viewReport">查看报告</el-button>
      </div>
    </div>

    <el-descriptions v-if="detail" :column="3" border size="small" class="mb16">
      <el-descriptions-item label="类型">{{ detail.type }}</el-descriptions-item>
      <el-descriptions-item label="设备">#{{ detail.device_id ?? '-' }} ({{ detail.device_name ?? '-' }})</el-descriptions-item>
      <el-descriptions-item label="超时">{{ detail.timeout_seconds }}s</el-descriptions-item>
      <el-descriptions-item label="开始时间">{{ formatDateTime(detail.started_at) }}</el-descriptions-item>
      <el-descriptions-item label="结束时间">{{ formatDateTime(detail.finished_at) }}</el-descriptions-item>
      <el-descriptions-item label="耗时">{{ durationText(detail.duration) }}</el-descriptions-item>
      <el-descriptions-item label="执行参数" :span="3">
        <ExecutionParameters :parameters="detail.parameters" />
      </el-descriptions-item>
    </el-descriptions>

    <div class="detail-grid">
      <div class="content-card">
        <div class="v2-card-title">用例、步骤、断言</div>
        <ExecutionTimeline v-if="detail" :cases="timelineCases" />
      </div>
      <div class="content-card log-card">
        <div class="v2-card-title">实时日志</div>
        <LiveLogViewer :logs="logEntries" :connected="socket?.connected.value ?? false" :connecting="socket?.connecting.value ?? true" />
      </div>
    </div>

    <DevicePicker ref="picker" />
  </div>
</template>

<style scoped>
.head-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.head-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}
.head-meta {
  margin-top: 4px;
}
.head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.mb16 {
  margin-bottom: 16px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.log-card {
  min-height: 300px;
}
</style>
