<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { executionStatusMeta } from '@/api/executions'
import { downloadReport, getReportDetail, reportFileUrl, type ReportDetail } from '@/api/reports'
import AuthenticatedImage from '@/components/AuthenticatedImage.vue'
import DevicePicker from '@/components/DevicePicker.vue'
import { useExecutionRetry } from '@/composables/useExecutionRetry'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'
import { applyOnlyFailed } from '@/utils/reportFilter'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const navigation = useWorkspaceNavigation()
// Step 7：reportId 改为 computed/watch，路由复用时重新加载
const reportId = computed(() => Number(route.params.reportId ?? route.params.id))

const loading = ref(false)
const detail = ref<ReportDetail | null>(null)
// Step 7：activeCases 存 case.id（el-collapse name 必须是 case.id，而非过滤后数组索引）
const activeCases = ref<number[]>([])
const onlyFailed = ref(false)
let loadedReportId: number | null = null

const { picker, retry: retryEntry, running: retrying } = useExecutionRetry()

const displayCases = computed(() => {
  if (!detail.value) return []
  if (!onlyFailed.value) return detail.value.cases
  return detail.value.cases.filter((c) => ['failed', 'error'].includes(c.status))
})

function typeLabel(t: unknown) {
  return { case: '用例', suite: '套件', batch: '批量' }[t as string] ?? '-'
}

function durationText(ms: unknown) {
  const n = Number(ms)
  if (!n) return '-'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`
}

function levelType(level: string) {
  if (level === 'ERROR') return 'danger'
  if (level === 'WARN') return 'warning'
  if (level === 'DEBUG') return 'info'
  return 'primary'
}

function statusMeta(s: unknown) {
  return executionStatusMeta(String(s))
}

function expandAll() {
  activeCases.value = displayCases.value.map((c) => c.id)
}

function collapseAll() {
  activeCases.value = []
}

// Step 7：checkbox 只使用 v-model；handler 接收新 boolean，不再自行反转
function onOnlyFailedChange(value: string | number | boolean) {
  if (!detail.value) return
  activeCases.value = applyOnlyFailed(
    activeCases.value,
    Boolean(value),
    detail.value.cases,
  )
}

async function load() {
  const id = reportId.value
  if (!Number.isFinite(id) || id === loadedReportId) return
  loadedReportId = id
  loading.value = true
  try {
    const data = await getReportDetail(id)
    await navigation.normalizeProjectDetail('report', id, data.execution.project_id)
    detail.value = data
    // 首次加载默认展开 failed/error
    activeCases.value = detail.value.cases
      .map((c) => (['failed', 'error'].includes(c.status) ? c.id : -1))
      .filter((cid) => cid >= 0)
  } finally {
    loading.value = false
  }
}

watch(reportId, load, { immediate: true })

async function download() {
  try {
    await downloadReport(reportId.value)
    ElMessage.success('报告已生成并下载')
  } catch {
    ElMessage.error('报告生成失败')
  }
}

async function retryThis() {
  // Step 5：不要再把 execution ID 伪装成 case ID；走 retry target（{device_id, timeout_seconds?}）
  const execId = Number(detail.value?.execution?.id)
  if (!execId) return
  await retryEntry(execId, `执行 #${execId}`)
}

function viewExecution() {
  const execId = Number(detail.value?.execution.id)
  if (execId) void router.push(navigation.executionDetail(execId))
}
</script>

<template>
  <div v-loading="loading">
    <div v-if="detail" class="wrap">
      <div class="card head">
        <div class="head-row">
          <h1>执行报告 #{{ detail.execution.id }}</h1>
          <el-tag :type="statusMeta(detail.execution.status).type" size="small">
            {{ statusMeta(detail.execution.status).label }}
          </el-tag>
          <div class="head-actions">
            <el-button @click="viewExecution">查看执行</el-button>
            <el-button :loading="retrying" @click="retryThis">重试</el-button>
            <el-button @click="navigation.back('report')">返回</el-button>
            <el-button type="primary" @click="download">下载 HTML 报告</el-button>
          </div>
        </div>
        <div class="meta-grid">
          <div class="meta"><span class="label">类型</span>{{ typeLabel(detail.execution.type) }}</div>
          <div class="meta"><span class="label">设备</span>#{{ detail.execution.device_id ?? '自动' }}</div>
          <div class="meta"><span class="label">超时</span>{{ detail.execution.timeout_seconds }}s</div>
          <div class="meta"><span class="label">开始</span>{{ formatDateTime(detail.execution.started_at) }}</div>
          <div class="meta"><span class="label">结束</span>{{ formatDateTime(detail.execution.finished_at) }}</div>
          <div class="meta"><span class="label">耗时</span>{{ durationText(detail.execution.duration) }}</div>
        </div>
      </div>

      <div class="card">
        <div class="stats">
          <div class="stat"><div class="num blue">{{ detail.report.total }}</div><div class="label">总用例</div></div>
          <div class="stat"><div class="num green">{{ detail.report.passed }}</div><div class="label">通过</div></div>
          <div class="stat"><div class="num red">{{ detail.report.failed }}</div><div class="label">失败</div></div>
          <div class="stat"><div class="num orange">{{ detail.report.error_count }}</div><div class="label">异常</div></div>
          <div class="stat"><div class="num gray">{{ detail.report.skipped }}</div><div class="label">跳过</div></div>
          <div class="stat"><div class="num blue">{{ detail.report.success_rate }}%</div><div class="label">成功率</div></div>
        </div>
      </div>

      <div class="card">
        <div class="case-toolbar">
          <h2>用例明细</h2>
          <div class="case-actions">
            <el-checkbox v-model="onlyFailed" @change="onOnlyFailedChange">只看失败/异常</el-checkbox>
            <el-button size="small" @click="expandAll">全部展开</el-button>
            <el-button size="small" @click="collapseAll">全部折叠</el-button>
          </div>
        </div>
        <el-collapse v-model="activeCases">
          <el-collapse-item v-for="c in displayCases" :key="c.id" :name="c.id">
            <template #title>
              <span class="case-name">{{ c.case_name }}</span>
              <el-tag :type="statusMeta(c.status).type" size="small">{{ statusMeta(c.status).label }}</el-tag>
              <span class="case-dur">{{ durationText(c.duration) }}</span>
            </template>
            <div v-if="c.error_message" class="error-text">{{ c.error_message }}</div>

            <el-table v-if="c.steps.length" :data="c.steps" size="small">
              <el-table-column prop="step_order" label="#" width="50" />
              <el-table-column prop="action" label="动作" width="120" />
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'passed' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="耗时" width="90">
                <template #default="{ row }">{{ durationText(row.duration) }}</template>
              </el-table-column>
              <el-table-column prop="actual_value" label="实际值" min-width="120" show-overflow-tooltip />
              <el-table-column prop="error_message" label="错误" min-width="160" show-overflow-tooltip />
              <el-table-column label="截图" width="130">
                <template #default="{ row }">
                  <AuthenticatedImage
                    v-if="row.screenshot"
                    :src="reportFileUrl(reportId, row.screenshot)"
                    class="thumb"
                  />
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="无步骤" :image-size="60" />

            <el-table v-if="c.assertions.length" :data="c.assertions" size="small" class="mt8">
              <el-table-column prop="assertion_type" label="断言" width="150" />
              <el-table-column prop="expected_value" label="期望" min-width="120" show-overflow-tooltip />
              <el-table-column prop="actual_value" label="实际" min-width="120" show-overflow-tooltip />
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="['pass', 'passed'].includes(row.status) ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="error_message" label="错误" min-width="140" show-overflow-tooltip />
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </div>

      <div class="card">
        <h2>执行日志</h2>
        <div v-if="detail.logs_truncated" class="truncate-note v2-aux">
          日志总量 {{ detail.logs_total }} 条，仅展示最后 {{ detail.logs.length }} 条
        </div>
        <el-table v-if="detail.logs.length" :data="detail.logs" size="small">
          <el-table-column prop="level" label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="levelType(row.level)" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="message" label="消息" min-width="300" show-overflow-tooltip />
          <el-table-column prop="source" label="来源" width="90" />
          <el-table-column label="时间" width="180">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="暂无日志" :image-size="60" />
      </div>
    </div>

    <DevicePicker ref="picker" />
  </div>
</template>

<style scoped>
.wrap {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.card {
  background: #fff;
  border-radius: 8px;
  padding: 18px 22px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.head-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.head-actions {
  margin-left: auto;
}
.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.meta {
  font-size: 14px;
}
.meta .label {
  color: #909399;
  font-size: 12px;
  margin-right: 8px;
}
.stats {
  display: flex;
  gap: 28px;
  flex-wrap: wrap;
}
.stat .num {
  font-size: 24px;
  font-weight: 700;
}
.stat .num.green {
  color: #67c23a;
}
.stat .num.red {
  color: #f56c6c;
}
.stat .num.orange {
  color: #e6a23c;
}
.stat .num.gray {
  color: #909399;
}
.stat .num.blue {
  color: #409eff;
}
.stat .label {
  color: #909399;
  font-size: 12px;
}
.case-name {
  font-weight: 600;
  margin-right: 10px;
}
.case-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.case-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.case-dur {
  color: #999;
  font-size: 12px;
  margin-left: 10px;
}
.error-text {
  color: #f56c6c;
  margin-bottom: 8px;
}
.truncate-note {
  color: #e6a23c;
  margin-bottom: 8px;
}
.thumb {
  width: 60px;
  height: 80px;
  border-radius: 4px;
  border: 1px solid #eee;
}
.mt8 {
  margin-top: 8px;
}
</style>
