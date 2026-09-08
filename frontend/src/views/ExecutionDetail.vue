<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getExecution,
  getExecutionLogs,
  canRetryExecution,
  stopExecution,
  type ExecutionDetail,
  type ExecutionLog,
  type ExecutionStatus,
} from '@/api/executions'
import { findReportByExecution } from '@/api/reports'
import DevicePicker from '@/components/DevicePicker.vue'
import ExecutionParameters from '@/components/ExecutionParameters.vue'
import ExecutionTimeline, { type TimelineSuite } from '@/components/ExecutionTimeline.vue'
import LiveLogViewer, { type LogEntry } from '@/components/LiveLogViewer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useExecutionRetry } from '@/composables/useExecutionRetry'
import { useExecutionSocket } from '@/composables/useExecutionSocket'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'
import { getToken } from '@/utils/request'
import {
  appendLogs,
  liveLogKey,
  mergeExecutionLogs,
  nextLogCursor,
  type LogLike,
} from '@/utils/executionLogs'
import {
  applyAssertionResult,
  applyCaseStatus,
  applyNodeStarted,
  applyNodeResult,
  applyStepResult,
  applySuiteStatus,
  settleExecutionSuites,
} from '@/utils/executionRealtime'
import {
  extractActiveExecutionTarget,
  findRunningExecutionTarget,
  type ActiveExecutionTarget,
} from '@/utils/executionAutofollow'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const navigation = useWorkspaceNavigation()
// Step 7：executionId 改 computed，路由复用/参数变化时重载
const executionId = computed(() => Number(route.params.executionId))

const loading = ref(false)
// 执行快照体积可能很大；时间线使用独立浅引用，只在收到消息时显式换引用刷新。
const detail = shallowRef<ExecutionDetail | null>(null)
const stopping = ref(false)
const reportId = ref<number | null>(null)

// REST 日志（后端 id 去重）与 WS live 日志（单调递减临时 id，避免 Date.now 碰撞）
const logs = ref<ExecutionLog[]>([])
const liveLogs = ref<LogEntry[]>([])
let liveIdCounter = 0
let logKeys = new Set<string>()
let completedPulled = false
let realtimeVersion = 0

const socket = shallowRef<ReturnType<typeof useExecutionSocket> | null>(null)
let staleId = 0 // loadAll 的异步完成检查：只允许当前路由的请求生效

const timelineSuites = shallowRef<TimelineSuite[]>([])
const activeTarget = ref<ActiveExecutionTarget | null>(null)

function toTimelineSuites(suites: ExecutionDetail['suites']): TimelineSuite[] {
  return (suites ?? []).map((s) => ({
    id: s.id,
    suite_id: s.suite_id,
    suite_name: s.suite_name,
    suite_order: s.suite_order,
    is_virtual: s.is_virtual ?? false,
    status: s.status,
    duration: s.duration,
    error_message: s.error_message,
    setup_steps: (s.setup_steps ?? []).map(toTimelineStep),
    cases: (s.cases ?? []).map((c) => ({
      id: c.id,
      case_id: c.case_id,
      case_name: c.case_name,
      status: c.status,
      duration: c.duration,
      error_message: c.error_message,
      steps: (c.steps ?? []).map(toTimelineStep),
      nodes: c.nodes,
    })),
    teardown_steps: (s.teardown_steps ?? []).map(toTimelineStep),
  }))
}

function toTimelineStep(s: {
  id: number
  step_order: number
  action: string
  phase?: string
  parameters: Record<string, unknown>
  status: string
  duration: number | null
  actual_value: string | null
  error_message: string | null
  artifact_id?: string | null
  assertions?: Array<{
    id: number
    assertion_order?: number | null
    assertion_type: string
    expected_value: string | null
    actual_value: string | null
    status: string
    error_message: string | null
    params?: Record<string, unknown> | null
    description?: string | null
  }>
}) {
  return {
    id: s.id,
    step_order: s.step_order,
    action: s.action,
    phase: s.phase as never,
    parameters: s.parameters,
    status: s.status,
    duration: s.duration,
    actual_value: s.actual_value,
    error_message: s.error_message,
    artifact_id: s.artifact_id ?? null,
    assertions: (s.assertions ?? []).map((assertion) => ({
      ...assertion,
      assertion_order: assertion.assertion_order ?? assertion.id,
    })),
  }
}

function updateExecutionStatus(status: ExecutionStatus) {
  if (!detail.value) return
  if (isTerminal(status)) activeTarget.value = null
  if (detail.value.status === status) return
  detail.value = { ...detail.value, status }
}

function notifyTimelineChanged() {
  // 仅复制套件顶层数组，不重新映射整棵用例/步骤树。
  timelineSuites.value = [...timelineSuites.value]
}

const logEntries = computed<LogEntry[]>(() => {
  const live: LogLike[] = liveLogs.value.map((l) => ({ id: l.id, level: l.level, message: l.message, source: l.source, created_at: l.created_at }))
  return mergeExecutionLogs(logs.value, live) as LogEntry[]
})

const logTerminal = computed(() => isTerminal(detail.value?.status ?? ''))
const logConnected = computed(() => socket.value?.connected.value ?? false)
const logConnecting = computed(() => {
  if (logTerminal.value) return false
  return socket.value?.connecting.value ?? loading.value
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

// 全量分页拉取执行日志：循环翻页直到累计 ≥ total 或空页；页数上限 50（=10000 条），超过则不再分页（极端场景保护）。
async function fetchAllLogs(id: number): Promise<ExecutionLog[]> {
  const pageSize = 200
  const maxPages = 50
  let all: ExecutionLog[] = []
  for (let page = 1; page <= maxPages; page++) {
    const data = await getExecutionLogs(id, { page, page_size: pageSize })
    all = [...all, ...data.items]
    if (data.items.length === 0 || all.length >= data.total) break
  }
  return all
}

async function loadAll(id: number) {
  const myStale = ++staleId
  loading.value = true
  activeTarget.value = null
  try {
    const data = await getExecution(id)
    if (staleId !== myStale) return // 旧请求晚到，不覆盖当前路由数据
    await navigation.normalizeProjectDetail('execution', id, data.project_id)
    if (staleId !== myStale) return
    detail.value = data
    timelineSuites.value = toTimelineSuites(data.suites)
    activeTarget.value = findRunningExecutionTarget(timelineSuites.value)
    resetLogs()
    const allLogs = await fetchAllLogs(id)
    if (staleId !== myStale) return
    logs.value = allLogs
    reportId.value = null
    completedPulled = false
    if (isTerminal(data.status)) {
      activeTarget.value = null
      socket.value?.close()
      socket.value = null
      const rid = await findReportByExecution(id)
      if (staleId === myStale) reportId.value = rid
      return
    }
    subscribe(id)
  } finally {
    loading.value = false
  }
}

async function resyncAfterConnect(id: number) {
  const myStale = staleId
  const versionAtStart = realtimeVersion
  try {
    const data = await getExecution(id)
    // 请求期间若已有实时增量到达，旧 REST 快照不得覆盖新状态。
    if (staleId !== myStale || executionId.value !== id || realtimeVersion !== versionAtStart) return
    detail.value = data
    timelineSuites.value = toTimelineSuites(data.suites)
    activeTarget.value = findRunningExecutionTarget(timelineSuites.value)
    // 断线窗口补拉日志：以当前已显示日志（含 live，即 logEntries）的最大时间为游标，
    // 把新的 REST 行并入 logs.value（appendLogs 按 id 去重、live 容差去重交给 logEntries computed）。
    const cursor = nextLogCursor(logEntries.value)
    if (cursor) {
      const page = await getExecutionLogs(id, { after_timestamp: cursor, page_size: 200 })
      if (staleId === myStale && executionId.value === id && realtimeVersion === versionAtStart) {
        logs.value = appendLogs(logs.value, page.items) as ExecutionLog[]
      }
    }
    if (isTerminal(data.status)) {
      activeTarget.value = null
      socket.value?.close()
      const rid = await findReportByExecution(id)
      if (staleId === myStale && executionId.value === id) reportId.value = rid
    }
  } catch {
    // 初始 loadAll 已有可展示数据；重连补拉失败时继续依赖后续 WS 增量。
  }
}

function subscribe(id: number) {
  const token = getToken() ?? ''
  // Step 7：subscribe 前强制关闭旧 socket（防止新老执行串流）
  socket.value?.close()
  const ws = useExecutionSocket(id, token, (msg) => {
    if (msg.execution_id != null && Number(msg.execution_id) !== id) return
    const type = msg.type as string
    if (type === 'status') {
      updateExecutionStatus((msg.status as ExecutionStatus) ?? detail.value?.status ?? 'queued')
    } else if (type === 'log') {
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
      realtimeVersion += 1
      applyStepResult(timelineSuites.value, msg)
      notifyTimelineChanged()
    } else if (type === 'assertion_result') {
      realtimeVersion += 1
      applyAssertionResult(timelineSuites.value, msg)
      notifyTimelineChanged()
    } else if (type === 'node_started') {
      realtimeVersion += 1
      applyNodeStarted(timelineSuites.value, msg)
      activeTarget.value = extractActiveExecutionTarget(msg)
      notifyTimelineChanged()
    } else if (type === 'node_result') {
      realtimeVersion += 1
      applyNodeResult(timelineSuites.value, msg)
      notifyTimelineChanged()
    } else if (type === 'case_status') {
      realtimeVersion += 1
      applyCaseStatus(timelineSuites.value, msg)
      notifyTimelineChanged()
    } else if (type === 'suite_status') {
      realtimeVersion += 1
      applySuiteStatus(timelineSuites.value, msg)
      notifyTimelineChanged()
    } else if (type === 'completed') {
      // Step 7：completed 只触发一次 REST 补拉并关闭 socket
      if (completedPulled) return
      completedPulled = true
      realtimeVersion += 1
      if (detail.value) {
        const status = (msg.status as ExecutionStatus) ?? detail.value.status
        updateExecutionStatus(status)
        settleExecutionSuites(timelineSuites.value, status)
        activeTarget.value = null
        notifyTimelineChanged()
      }
      ws.close()
      void loadAll(id)
    }
  }, () => void resyncAfterConnect(id))
  socket.value = ws
}

const timelineRef = ref<InstanceType<typeof ExecutionTimeline> | null>(null)

const { picker, retry: retryEntry, running: retrying } = useExecutionRetry()
const canRetry = computed(() => canRetryExecution(detail.value?.status))

async function stop() {
  stopping.value = true
  try {
    await stopExecution(executionId.value)
    ElMessage.success('已请求停止')
    updateExecutionStatus('stopping')
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
    socket.value?.close()
    socket.value = null
    detail.value = null
    timelineSuites.value = []
    activeTarget.value = null
    logs.value = []
    liveLogs.value = []
    reportId.value = null
    completedPulled = false
    realtimeVersion = 0
    if (Number.isFinite(id)) void loadAll(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => socket.value?.close())
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
        <el-button v-if="canRetry" type="primary" :loading="retrying" @click="retry">重试</el-button>
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
        <div class="card-head-row">
          <div class="v2-card-title">套件、用例、步骤、断言</div>
          <div class="card-actions">
            <el-button size="small" text @click="timelineRef?.expandAll()">全部展开</el-button>
            <el-button size="small" text @click="timelineRef?.collapseAll()">全部折叠</el-button>
          </div>
        </div>
        <ExecutionTimeline
          ref="timelineRef"
          :key="executionId"
          :suites="timelineSuites"
          :active-target="activeTarget"
        />
      </div>
      <div class="content-card log-card">
        <div class="v2-card-title">实时日志</div>
        <LiveLogViewer :logs="logEntries" :connected="logConnected" :connecting="logConnecting" :terminal="logTerminal" />
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
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}
/* grid 项允许收缩到内容最小宽度以下，避免长文本把相邻列挤扁 */
.detail-grid > * {
  min-width: 0;
}
.card-head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}
.card-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.log-card {
  height: 100%;
  max-height: 560px;
  min-height: 300px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
@media (max-width: 1023px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
