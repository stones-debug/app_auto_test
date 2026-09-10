<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  EXECUTION_STATUS,
  canRetryExecution,
  listExecutions,
  stopExecution,
  type ExecutionListItem,
} from '@/api/executions'
import StatusBadge from '@/components/StatusBadge.vue'
import DevicePicker from '@/components/DevicePicker.vue'
import { getDashboardOverview } from '@/api/dashboard'
import { useExecutionRetry } from '@/composables/useExecutionRetry'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'
import { withProjectScope } from '@/navigation/workspaceScope'
import { formatDateTime } from '@/utils/format'

const navigation = useWorkspaceNavigation()
const { projectId, isProjectWorkspace } = navigation
const router = useRouter()
const loading = ref(false)
const items = ref<ExecutionListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const statusFilter = ref('')
const typeFilter = ref('')
const keyword = ref('')
const summary = ref<{ active: number; failed: number; error: number; passed: number }>({ active: 0, failed: 0, error: 0, passed: 0 })
let timer: ReturnType<typeof setInterval> | null = null
// 竞态保护：轮询与用户翻页可能并发，旧响应后到会覆盖新页数据。
let loadSeq = 0

const { picker, retry: retryEntry } = useExecutionRetry()

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const data = await listExecutions(withProjectScope(projectId.value, {
      page: page.value,
      page_size: pageSize.value,
      status: statusFilter.value,
      type: typeFilter.value,
      keyword: keyword.value || undefined,
    }))
    if (seq !== loadSeq) return
    items.value = data.items
    total.value = data.total
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

/** 轮询后校正越界页：新执行不断插入会把用户停留的页挤出范围，导致整页空白。 */
async function reloadClamped() {
  await load()
  const lastPage = Math.max(1, Math.ceil(total.value / pageSize.value))
  if (page.value > lastPage) {
    page.value = lastPage
    await load()
  }
}

async function loadSummary() {  try {
    const data = await getDashboardOverview(withProjectScope(projectId.value, {}))
    const sc = data.status_counts
    summary.value = {
      active: sc.running + sc.stopping,
      failed: sc.failed,
      error: sc.error,
      passed: sc.passed,
    }
  } catch {
    /* 摘要失败不阻塞列表 */
  }
}

function typeLabel(t: string) {
  return { case: '用例', suite: '套件', batch: '批量' }[t] ?? t
}

function durationText(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function fmtTime(t: string | null | undefined) {
  return formatDateTime(t)
}

function isActive(status: string) {
  return ['queued', 'running', 'stopping'].includes(status)
}

function openDetail(id: number) {
  void router.push(navigation.executionDetail(id))
}

async function stop(row: ExecutionListItem) {
  await ElMessageBox.confirm('确认停止该执行？', '提示', { type: 'warning' })
  await stopExecution(row.id)
  ElMessage.success('已请求停止')
  await load()
}

async function retry(row: ExecutionListItem) {
  // Step 5：重试统一走 DevicePicker（携带 {device_id, timeout_seconds?}），成功后跳新执行详情
  await retryEntry(row.id, row.case_name ?? row.suite_name ?? `#${row.id}`)
}

function onSearch() {
  page.value = 1
  load()
}

watch(
  projectId,
  () => {
    page.value = 1
    void load()
    void loadSummary()
  },
  { immediate: true },
)

onMounted(() => {
  // 活跃执行每 5 秒轮询当前页（并校正越界页）
  timer = setInterval(() => {
    if (document.visibilityState === 'visible') {
      void reloadClamped()
      void loadSummary()
    }
  }, 5000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div>
    <!-- 状态摘要 -->
    <div class="summary-bar">
      <div class="summary-item" @click="statusFilter = 'running'; onSearch()">
        <span class="summary-value primary">{{ summary.active }}</span>
        <span class="summary-label">活跃执行</span>
      </div>
      <div class="summary-item" @click="statusFilter = 'passed'; onSearch()">
        <span class="summary-value success">{{ summary.passed }}</span>
        <span class="summary-label">已通过</span>
      </div>
      <div class="summary-item" @click="statusFilter = 'failed'; onSearch()">
        <span class="summary-value danger">{{ summary.failed }}</span>
        <span class="summary-label">失败</span>
      </div>
      <div class="summary-item" @click="statusFilter = 'error'; onSearch()">
        <span class="summary-value danger-dark">{{ summary.error }}</span>
        <span class="summary-label">异常</span>
      </div>
    </div>

    <div class="toolbar-card">
      <el-input v-model="keyword" placeholder="按名称/ID 搜索" clearable class="search" @keyup.enter="onSearch" />
      <el-select v-model="statusFilter" placeholder="状态" clearable class="w140" @change="onSearch">
        <el-option v-for="s in EXECUTION_STATUS" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-select v-model="typeFilter" placeholder="类型" clearable class="w110" @change="onSearch">
        <el-option label="用例" value="case" />
        <el-option label="套件" value="suite" />
        <el-option label="批量" value="batch" />
      </el-select>
      <el-button type="primary" @click="onSearch">搜索</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="items" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ typeLabel(row.type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="名称" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">{{ row.case_name ?? row.suite_name ?? '-' }}</template>
      </el-table-column>
      <el-table-column v-if="!isProjectWorkspace" label="项目" width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.project_name ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="设备" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.device_name ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <StatusBadge :status="row.status" />
        </template>
      </el-table-column>
      <el-table-column label="耗时" width="90">
        <template #default="{ row }">{{ durationText(row.duration) }}</template>
      </el-table-column>
      <el-table-column label="创建人" width="100" show-overflow-tooltip>
        <template #default="{ row }">{{ row.created_by_name ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" text @click="openDetail(row.id)">详情</el-button>
          <el-button v-if="isActive(row.status)" size="small" text @click="stop(row as ExecutionListItem)">停止</el-button>
          <el-button v-if="canRetryExecution(row.status)" size="small" text @click="retry(row as ExecutionListItem)">重试</el-button>
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

    <DevicePicker ref="picker" />
  </div>
</template>

<style scoped>
.summary-bar {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.summary-item {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px 16px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.summary-item:hover {
  border-color: var(--primary);
}
.summary-value {
  font-size: var(--font-kpi);
  font-weight: 700;
}
.summary-value.primary {
  color: var(--primary);
}
.summary-value.success {
  color: var(--success);
}
.summary-value.danger {
  color: var(--danger);
}
.summary-value.danger-dark {
  color: var(--danger-dark);
}
.summary-label {
  font-size: var(--font-aux);
  color: var(--text-2);
}
.search {
  width: 200px;
}
.w140 {
  width: 140px;
}
.w110 {
  width: 110px;
}
</style>
