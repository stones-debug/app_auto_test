<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { executionStatusMeta } from '@/api/executions'
import { listReports, type ReportListItem } from '@/api/reports'

const router = useRouter()

const loading = ref(false)
const items = ref<ReportListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const statusFilter = ref('')
const typeFilter = ref('')
const keyword = ref('')
const summary = ref({ passed: 0, failed: 0, error: 0, success_rate: 0 })

async function load() {
  loading.value = true
  try {
    const data = await listReports({
      page: page.value,
      page_size: pageSize.value,
      ...(statusFilter.value ? { status: statusFilter.value } : {}),
      ...(typeFilter.value ? { type: typeFilter.value } : {}),
      ...(keyword.value ? { keyword: keyword.value } : {}),
    })
    items.value = data.items
    total.value = data.total
    const passed = data.items.filter((i) => i.execution_status === 'passed').length
    const failed = data.items.filter((i) => i.execution_status === 'failed').length
    const error = data.items.filter((i) => i.execution_status === 'error').length
    const rated = data.items.filter((i) => i.success_rate > 0)
    summary.value = {
      passed,
      failed,
      error,
      success_rate: rated.length ? rated.reduce((s, i) => s + i.success_rate, 0) / rated.length : 0,
    }
  } finally {
    loading.value = false
  }
}

function typeLabel(t: string | null) {
  return { case: '用例', suite: '套件', batch: '批量' }[t ?? ''] ?? '-'
}

function durationText(ms: number | null) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function openDetail(row: ReportListItem) {
  router.push(`/reports/${row.id}`)
}

onMounted(load)
</script>

<template>
  <div>
    <!-- 摘要（V2 §5.15） -->
    <div class="summary-bar">
      <div class="summary-item">
        <span class="summary-value success">{{ summary.passed }}</span>
        <span class="summary-label">当前页通过</span>
      </div>
      <div class="summary-item">
        <span class="summary-value danger">{{ summary.failed }}</span>
        <span class="summary-label">失败</span>
      </div>
      <div class="summary-item">
        <span class="summary-value danger-dark">{{ summary.error }}</span>
        <span class="summary-label">异常</span>
      </div>
      <div class="summary-item">
        <span class="summary-value primary">{{ summary.success_rate ? summary.success_rate.toFixed(1) + '%' : '—' }}</span>
        <span class="summary-label">平均成功率</span>
      </div>
    </div>

    <div class="toolbar-card">
      <el-input v-model="keyword" placeholder="按执行 ID 搜索" clearable class="search" @keyup.enter="page = 1; load()" />
      <el-select v-model="statusFilter" placeholder="执行状态" clearable class="status" @change="page = 1; load()">
        <el-option v-for="s in ['queued','running','stopping','passed','failed','error','stopped','cancelled']" :key="s" :label="executionStatusMeta(s).label" :value="s" />
      </el-select>
      <el-select v-model="typeFilter" placeholder="类型" clearable class="status" @change="page = 1; load()">
        <el-option label="用例" value="case" />
        <el-option label="套件" value="suite" />
        <el-option label="批量" value="batch" />
      </el-select>
      <el-button type="primary" @click="page = 1; load()">搜索</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="items">
      <el-table-column prop="id" label="报告ID" width="80" />
      <el-table-column prop="execution_id" label="执行ID" width="80" />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">{{ typeLabel(row.execution_type) }}</template>
      </el-table-column>
      <el-table-column label="名称" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">{{ row.case_name ?? row.suite_name ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.execution_status" :type="executionStatusMeta(row.execution_status).type" size="small">
            {{ executionStatusMeta(row.execution_status).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="通过/总数" width="110">
        <template #default="{ row }">
          <span class="ok">{{ row.passed }}</span> / {{ row.total }}
        </template>
      </el-table-column>
      <el-table-column label="成功率" width="90">
        <template #default="{ row }">{{ row.success_rate }}%</template>
      </el-table-column>
      <el-table-column label="耗时" width="90">
        <template #default="{ row }">{{ durationText(row.duration) }}</template>
      </el-table-column>
      <el-table-column label="HTML" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.has_report" type="success" size="small">已生成</el-tag>
          <el-tag v-else type="info" size="small">按需</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="时间" width="180" />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" text @click="openDetail(row as ReportListItem)">查看</el-button>
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
  display: flex;
  flex-direction: column;
  gap: 2px;
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
.status {
  width: 130px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.ok {
  color: #67c23a;
  font-weight: 600;
}
</style>
