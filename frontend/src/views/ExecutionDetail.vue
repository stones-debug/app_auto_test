<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getExecution,
  getExecutionLogs,
  retryExecution,
  stopExecution,
  type ExecutionDetail,
  type ExecutionLog,
  type ExecutionStatus,
} from '@/api/executions'
import { findReportByExecution } from '@/api/reports'
import ExecutionTimeline, { type TimelineCase } from '@/components/ExecutionTimeline.vue'
import LiveLogViewer, { type LogEntry } from '@/components/LiveLogViewer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useExecutionSocket } from '@/composables/useExecutionSocket'
import { getToken } from '@/utils/request'

const route = useRoute()
const router = useRouter()
const executionId = Number(route.params.executionId)

const loading = ref(false)
const detail = ref<ExecutionDetail | null>(null)
const logs = ref<ExecutionLog[]>([])
const connected = ref(false)
const connecting = ref(true)
const stopping = ref(false)
const reportId = ref<number | null>(null)

const timelineCases = computed<TimelineCase[]>(() => {
  return (detail.value?.cases ?? []).map((c) => ({
    case_id: c.case_id,
    case_name: c.case_name,
    status: c.status,
    steps: (c.steps ?? []).map((s) => ({
      step_order: s.step_order,
      action: s.action,
      status: s.status,
      duration: s.duration,
      actual_value: s.actual_value,
      error_message: s.error_message,
      artifact_id: null,
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

const logEntries = computed<LogEntry[]>(() =>
  logs.value.map((l) => ({ id: l.id, level: l.level, message: l.message, source: l.source, created_at: l.created_at })),
)

function isTerminal(status: string) {
  return ['passed', 'failed', 'error', 'stopped', 'cancelled'].includes(status)
}

async function loadAll() {
  loading.value = true
  try {
    detail.value = await getExecution(executionId)
    const logData = await getExecutionLogs(executionId, { page_size: 200 })
    logs.value = logData.items
    if (isTerminal(detail.value.status)) {
      connected.value = false
      connecting.value = false
      const rid = await findReportByExecution(executionId)
      reportId.value = rid
      return
    }
    subscribe()
  } finally {
    loading.value = false
  }
}

function subscribe() {
  if (!detail.value) return
  const token = getToken() ?? ''
  const ws = useExecutionSocket(detail.value.id, token, (msg) => {
    const type = msg.type as string
    if (type === 'status') {
      if (detail.value) detail.value.status = (msg.status as ExecutionStatus) ?? detail.value.status
    } else if (type === 'log' && msg.execution_id === executionId) {
      logs.value.push({
        id: Date.now(),
        execution_id: executionId,
        level: (msg.level as string) ?? 'INFO',
        message: (msg.message as string) ?? '',
        source: 'live',
        created_at: (msg.timestamp as string) ?? new Date().toISOString(),
      })
    } else if (type === 'step_result') {
      updateCaseStatus(Number(msg.case_id), (msg.status as string) ?? 'running')
    } else if (type === 'completed') {
      if (detail.value) detail.value.status = (msg.status as ExecutionStatus) ?? detail.value.status
      connected.value = false
      loadAll()
    }
  })
  socket = ws
}

function updateCaseStatus(caseId: number, status: string) {
  const ec = detail.value?.cases.find((c) => c.case_id === caseId)
  if (ec) ec.status = status
}

let socket: { connected: { value: boolean }; close: () => void } | null = null

async function stop() {
  stopping.value = true
  try {
    await stopExecution(executionId)
    ElMessage.success('已请求停止')
    if (detail.value) detail.value.status = 'stopping'
  } finally {
    stopping.value = false
  }
}

async function retry() {
  const exec = await retryExecution(executionId)
  ElMessage.success(`已创建重试执行 #${exec.id}`)
  router.push(`/executions/${exec.id}`)
}

function viewReport() {
  if (reportId.value != null) router.push(`/reports/${reportId.value}`)
}

function durationText(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

onMounted(loadAll)
onBeforeUnmount(() => socket?.close())
</script>

<template>
  <div v-loading="loading">
    <div class="head-bar">
      <div>
        <el-button size="small" text @click="router.push('/executions')">← 返回执行中心</el-button>
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
        <el-button type="primary" @click="retry">重试</el-button>
        <el-button v-if="reportId != null" type="success" @click="viewReport">查看报告</el-button>
      </div>
    </div>

    <el-descriptions v-if="detail" :column="3" border size="small" class="mb16">
      <el-descriptions-item label="类型">{{ detail.type }}</el-descriptions-item>
      <el-descriptions-item label="设备">#{{ detail.device_id ?? '-' }} ({{ detail.device_name ?? '-' }})</el-descriptions-item>
      <el-descriptions-item label="超时">{{ detail.timeout_seconds }}s</el-descriptions-item>
      <el-descriptions-item label="开始时间">{{ detail.started_at ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="结束时间">{{ detail.finished_at ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="耗时">{{ durationText(detail.duration) }}</el-descriptions-item>
    </el-descriptions>

    <div class="detail-grid">
      <div class="content-card">
        <div class="v2-card-title">用例、步骤、断言</div>
        <ExecutionTimeline v-if="detail" :cases="timelineCases" />
      </div>
      <div class="content-card log-card">
        <div class="v2-card-title">实时日志</div>
        <LiveLogViewer :logs="logEntries" :connected="connected" :connecting="connecting" />
      </div>
    </div>
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