<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { executionStatusMeta } from '@/api/executions'
import { downloadReport, getReportDetail, type ReportDetail, type ReportExclusion } from '@/api/reports'
import DevicePicker from '@/components/DevicePicker.vue'
import ExecutionParameters from '@/components/ExecutionParameters.vue'
import ReportCaseCard from '@/components/ReportCaseCard.vue'
import ReportStepTable from '@/components/ReportStepTable.vue'
import { useExecutionRetry } from '@/composables/useExecutionRetry'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'
import { buildExclusionTree, flattenTreeKeys, type ExclusionTreeNode } from '@/utils/exclusionTree'
import { formatDateTime } from '@/utils/format'
import { REPORT_LOG_PAGE_SIZE, visibleReportLogs } from '@/utils/reportLogs'
import {
  emptyDisclosureState,
  initialDisclosureState,
  prepareReportCases,
  prepareReportSuites,
  toggleDisclosureId,
  visibleDisclosureState,
  type ReportDisclosureState,
} from '@/utils/reportDisclosure'

const route = useRoute()
const router = useRouter()
const navigation = useWorkspaceNavigation()
// Step 7：reportId 改为 computed/watch，路由复用时重新加载
const reportId = computed(() => Number(route.params.reportId ?? route.params.id))

const loading = ref(false)
// 报告数据只读，避免 Vue 为大型套件/步骤树创建深层响应式代理。
const detail = shallowRef<ReportDetail | null>(null)
// 套件与用例各自维护 O(1) 的展开集合，避免每个 el-collapse 深度监听同一个数组。
const expandedSuites = shallowRef<Set<number>>(new Set())
const expandedCases = shallowRef<Set<number>>(new Set())
const onlyFailed = ref(false)
const logsExpanded = ref(false)
const visibleLogLimit = ref(REPORT_LOG_PAGE_SIZE)
let loadedReportId: number | null = null

const { picker, retry: retryEntry, running: retrying } = useExecutionRetry()

// 步骤在报告数据变化时只拆分一次，展开/收起不再重复过滤步骤数组。
const preparedSuites = computed(() => prepareReportSuites(detail.value?.suites ?? []))
const preparedCases = computed(() => (
  detail.value?.suites?.length ? [] : prepareReportCases(detail.value?.cases ?? [])
))

// 方案 §4.4：按 suites 分层；只看失败时每个套件内仅保失败用例。
const displaySuites = computed(() => {
  if (!onlyFailed.value) return preparedSuites.value
  return preparedSuites.value
    .map((s) => ({ ...s, cases: s.cases.filter((c) => ['failed', 'error'].includes(c.status)) }))
    .filter((s) => s.cases.length > 0)
})

// 单用例/历史兼容：无 suites 时回退扁平 cases
const displayCases = computed(() => {
  if (preparedSuites.value.length) return []
  if (!onlyFailed.value) return preparedCases.value
  return preparedCases.value.filter((c) => ['failed', 'error'].includes(c.status))
})

const visibleLogs = computed(() => visibleReportLogs(
  detail.value?.logs ?? [],
  visibleLogLimit.value,
))

const hasEarlierLogs = computed(() => (
  visibleLogs.value.length < (detail.value?.logs.length ?? 0)
))

// 方案 §7.2：不适用内容清单——套件→用例→步骤 独立层级树（builder 在 utils/exclusionTree.ts，可测试）
const activeExclusions = ref<string[]>([])

const exclusionTree = computed<ExclusionTreeNode[]>(() =>
  buildExclusionTree((detail.value?.exclusions ?? []) as ReportExclusion[]),
)

function expandAllExclusions() {
  activeExclusions.value = flattenTreeKeys(exclusionTree.value)
}

function collapseAllExclusions() {
  activeExclusions.value = []
}

function typeLabel(t: unknown) {
  return { case: '用例', suite: '套件', batch: '批量' }[t as string] ?? '-'
}

// 不适用内容的目标类型标签（层级树叶子项）
function typeLabel2(t: unknown) {
  const map: Record<string, string> = {
    suite: '套件',
    case: '用例',
    step: '步骤',
    assertion: '断言',
    suite_step: '套件步骤',
  }
  return map[t as string] ?? String(t ?? '-')
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

function applyDisclosureState(state: ReportDisclosureState) {
  expandedSuites.value = state.expandedSuites
  expandedCases.value = state.expandedCases
}

function toggleSuite(id: number) {
  expandedSuites.value = toggleDisclosureId(expandedSuites.value, id)
}

function toggleCase(id: number) {
  expandedCases.value = toggleDisclosureId(expandedCases.value, id)
}

function expandAll() {
  applyDisclosureState(visibleDisclosureState(displaySuites.value, displayCases.value))
}

function collapseAll() {
  applyDisclosureState(emptyDisclosureState())
}

// checkbox 只使用 v-model；切换筛选后按当前可见内容恢复原有自动展开口径。
function onOnlyFailedChange() {
  applyDisclosureState(visibleDisclosureState(displaySuites.value, displayCases.value))
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
    // 首次加载：默认收起；仅含失败/异常用例的套件与其失败用例自动展开（与 HTML 报告初始状态一致）
    applyDisclosureState(initialDisclosureState(data.suites ?? [], data.cases ?? []))
    activeExclusions.value = []
    logsExpanded.value = false
    visibleLogLimit.value = REPORT_LOG_PAGE_SIZE
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
          <div class="meta">
            <span class="label">APP 档案</span>
            {{ detail.execution.app_profile_name ?? '历史兼容执行（未指定档案）' }}
          </div>
          <div class="meta"><span class="label">发布版本</span>{{ detail.execution.app_release_version ?? '-' }}</div>
          <div class="meta"><span class="label">配置修订</span>{{ detail.execution.profile_revision ?? '-' }}</div>
          <div class="meta"><span class="label">资产修订</span>{{ detail.execution.test_asset_revision ?? '-' }}</div>
        </div>
        <div class="execution-parameters">
          <div class="parameter-title">执行参数</div>
          <ExecutionParameters :parameters="detail.execution.parameters" />
        </div>
      </div>

      <div class="stat-group">
        <div class="stat-title">用例统计</div>
        <div class="stats">
          <div class="stat-card"><div class="num blue">{{ detail.report.total }}</div><div class="label">用例总数</div></div>
          <div class="stat-card"><div class="num green">{{ detail.report.passed }}</div><div class="label">通过</div></div>
          <div class="stat-card"><div class="num red">{{ detail.report.failed }}</div><div class="label">失败</div></div>
          <div class="stat-card"><div class="num orange">{{ detail.report.error_count }}</div><div class="label">异常</div></div>
          <div class="stat-card"><div class="num gray">{{ detail.report.skipped }}</div><div class="label">跳过</div></div>
          <div class="stat-card"><div class="num blue">{{ detail.report.success_rate }}%</div><div class="label">成功率</div></div>
          <div class="stat-card"><div class="num gray">{{ detail.report.not_applicable ?? 0 }}</div><div class="label">不适用</div></div>
        </div>
      </div>

      <div v-if="detail.report.suite_total != null" class="stat-group">
        <div class="stat-title">套件统计</div>
        <div class="stats">
          <div class="stat-card"><div class="num blue">{{ detail.report.suite_total }}</div><div class="label">套件总数</div></div>
          <div class="stat-card"><div class="num green">{{ detail.report.suite_passed }}</div><div class="label">套件通过</div></div>
          <div class="stat-card"><div class="num red">{{ detail.report.suite_failed }}</div><div class="label">套件失败</div></div>
          <div class="stat-card"><div class="num orange">{{ detail.report.suite_error_count }}</div><div class="label">套件异常</div></div>
          <div class="stat-card"><div class="num gray">{{ detail.report.suite_skipped }}</div><div class="label">套件跳过</div></div>
          <div class="stat-card"><div class="num blue">{{ detail.report.suite_success_rate }}%</div><div class="label">套件成功率</div></div>
          <div class="stat-card"><div class="num gray">{{ detail.report.not_applicable_suites ?? 0 }}</div><div class="label">不适用套件</div></div>
        </div>
      </div>

      <div v-if="detail.report.step_total != null" class="stat-group">
        <div class="stat-title">步骤统计</div>
        <div class="stats">
          <div class="stat-card"><div class="num blue">{{ detail.report.step_total }}</div><div class="label">步骤总数</div></div>
          <div class="stat-card"><div class="num green">{{ detail.report.step_passed }}</div><div class="label">步骤通过</div></div>
          <div class="stat-card"><div class="num red">{{ detail.report.step_failed }}</div><div class="label">步骤失败</div></div>
          <div class="stat-card"><div class="num orange">{{ detail.report.step_error_count }}</div><div class="label">步骤异常</div></div>
          <div class="stat-card"><div class="num gray">{{ detail.report.step_skipped }}</div><div class="label">步骤跳过</div></div>
          <div class="stat-card"><div class="num blue">{{ detail.report.step_success_rate }}%</div><div class="label">步骤成功率</div></div>
        </div>
      </div>

      <div v-if="exclusionTree.length" class="card">
        <div class="case-toolbar">
          <h2>不适用内容</h2>
          <div class="case-actions">
            <el-button size="small" @click="expandAllExclusions">全部展开</el-button>
            <el-button size="small" @click="collapseAllExclusions">全部折叠</el-button>
          </div>
        </div>
        <!-- 方案 §7.2：独立层级树（套件 → 用例 → 步骤/断言/套件步；无上下文时叶子独立展示） -->
        <el-collapse v-model="activeExclusions" class="exclusion-collapse">
          <template v-for="node in exclusionTree" :key="node.key">
            <template v-if="node.children.length">
              <el-collapse-item :name="node.key">
                <template #title>
                  <el-tag size="small" class="mr8" :type="node.targetType ? 'danger' : 'info'">套件</el-tag>
                  <span class="exclusion-path">{{ node.name }}</span>
                </template>
                <el-collapse v-model="activeExclusions" class="exclusion-sub">
                  <el-collapse-item v-for="child in node.children" :key="child.key" :name="child.key">
                    <template #title>
                      <el-tag size="small" class="mr8" :type="child.targetType ? 'danger' : 'warning'">{{ child.targetType === 'suite' ? '套件' : '用例' }}</el-tag>
                      <span class="exclusion-path">{{ child.name }}</span>
                    </template>
                    <template v-if="child.children.length">
                      <div v-for="leaf in child.children" :key="leaf.key" class="exclusion-row">
                        <el-tag size="small" type="danger" class="mr8">{{ typeLabel2(leaf.targetType) }}</el-tag>
                        <span class="exclusion-path">{{ leaf.name }}</span>
                        <span class="exclusion-detail">
                          <span>差异：{{ leaf.reasonCode }}</span>
                          <span v-if="leaf.reasonNote">备注：{{ leaf.reasonNote }}</span>
                          <span>来源：{{ leaf.sourceType }}</span>
                        </span>
                      </div>
                    </template>
                    <div v-else class="exclusion-detail">
                      <span>差异：{{ child.reasonCode }}</span>
                      <span v-if="child.reasonNote">备注：{{ child.reasonNote }}</span>
                      <span>来源：{{ child.sourceType }}</span>
                    </div>
                  </el-collapse-item>
                </el-collapse>
              </el-collapse-item>
            </template>
            <template v-else>
              <el-collapse-item :name="node.key">
                <template #title>
                  <el-tag size="small" type="danger" class="mr8">{{ typeLabel2(node.targetType) }}</el-tag>
                  <span class="exclusion-path">{{ node.name }}</span>
                </template>
                <div class="exclusion-detail">
                  <span>差异：{{ node.reasonCode }}</span>
                  <span v-if="node.reasonNote">备注：{{ node.reasonNote }}</span>
                  <span>来源：{{ node.sourceType }}</span>
                </div>
              </el-collapse-item>
            </template>
          </template>
        </el-collapse>
      </div>

      <div class="card">
        <div class="case-toolbar">
          <h2>{{ detail.suites?.length ? '套件明细' : '用例明细' }}</h2>
          <div class="case-actions">
            <el-checkbox v-model="onlyFailed" @change="onOnlyFailedChange">只看失败/异常</el-checkbox>
            <el-button size="small" @click="expandAll">全部展开</el-button>
            <el-button size="small" @click="collapseAll">全部折叠</el-button>
          </div>
        </div>

        <!-- 轻量级分层折叠：只挂载已展开的套件/用例内容，不使用高度动画。 -->
        <div v-if="detail.suites?.length" class="report-tree">
          <section v-for="s in displaySuites" :key="s.id" class="suite-card">
            <button
              type="button"
              class="disclosure-header suite-header"
              :class="{ 'is-active': expandedSuites.has(s.id) }"
              :aria-expanded="expandedSuites.has(s.id)"
              @click="toggleSuite(s.id)"
            >
              <span class="disclosure-caret" :class="{ 'is-open': expandedSuites.has(s.id) }" aria-hidden="true">▸</span>
              <span class="case-name">{{ s.suite_name }}</span>
              <el-tag :type="statusMeta(s.status).type" size="small">{{ statusMeta(s.status).label }}</el-tag>
              <span v-if="s.suite_id" class="case-dur">#{{ s.suite_id }}</span>
              <span class="case-dur">{{ durationText(s.duration) }}</span>
            </button>

            <div v-if="expandedSuites.has(s.id)" class="suite-body">
              <div v-if="s.error_message" class="error-box">{{ s.error_message }}</div>

              <template v-if="s.setup_steps.length">
                <div class="suite-phase">套件前置</div>
                <ReportStepTable :steps="s.setup_steps" :report-id="reportId" />
              </template>

              <ReportCaseCard
                v-for="c in s.cases"
                :key="c.id"
                :case-item="c"
                :report-id="reportId"
                :expanded="expandedCases.has(c.id)"
                @toggle="toggleCase(c.id)"
              />

              <template v-if="s.teardown_steps.length">
                <div class="suite-phase">套件后置</div>
                <ReportStepTable :steps="s.teardown_steps" :report-id="reportId" />
              </template>
            </div>
          </section>
        </div>

        <!-- 单用例/无套件上下文时回退扁平 cases，并复用完全相同的用例卡片。 -->
        <div v-else class="report-tree">
          <ReportCaseCard
            v-for="c in displayCases"
            :key="c.id"
            :case-item="c"
            :report-id="reportId"
            :expanded="expandedCases.has(c.id)"
            @toggle="toggleCase(c.id)"
          />
        </div>
      </div>

      <div v-if="detail.report.assertion_total != null" class="stat-group">
        <div class="stat-title">断言统计</div>
        <div class="stats">
          <div class="stat-card"><div class="num blue">{{ detail.report.assertion_total }}</div><div class="label">断言总数</div></div>
          <div class="stat-card"><div class="num green">{{ detail.report.assertion_passed }}</div><div class="label">断言通过</div></div>
          <div class="stat-card"><div class="num red">{{ detail.report.assertion_failed }}</div><div class="label">断言失败</div></div>
          <div class="stat-card"><div class="num orange">{{ detail.report.assertion_error_count }}</div><div class="label">断言异常</div></div>
          <div class="stat-card"><div class="num gray">{{ detail.report.assertion_skipped }}</div><div class="label">断言跳过</div></div>
        </div>
      </div>

      <div class="card">
        <div class="log-toolbar">
          <h2>执行日志（{{ detail.logs_total ?? detail.logs.length }}）</h2>
          <el-button
            v-if="detail.logs.length"
            size="small"
            text
            :aria-expanded="logsExpanded"
            @click="logsExpanded = !logsExpanded"
          >{{ logsExpanded ? '收起日志' : '展开日志' }}</el-button>
        </div>
        <template v-if="logsExpanded">
          <div v-if="detail.logs_truncated" class="truncate-note v2-aux">
            日志总量 {{ detail.logs_total }} 条，服务端仅保留最后 {{ detail.logs.length }} 条用于展示
          </div>
          <div v-if="hasEarlierLogs" class="log-load-more">
            <el-button size="small" @click="visibleLogLimit += REPORT_LOG_PAGE_SIZE">
              加载更早日志（当前 {{ visibleLogs.length }}/{{ detail.logs.length }}）
            </el-button>
          </div>
          <el-table v-if="visibleLogs.length" :data="visibleLogs" size="small">
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
        </template>
        <el-empty v-else-if="!detail.logs.length" description="暂无日志" :image-size="60" />
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
.execution-parameters {
  margin-top: 14px;
}
.parameter-title {
  margin-bottom: 6px;
  color: #909399;
  font-size: 12px;
}
.stat-group {
  margin-bottom: 2px;
}
.stat-title {
  color: #909399;
  font-size: 13px;
  font-weight: 600;
  margin: 4px 0 10px;
}
.stats {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.stat-card {
  min-width: 110px;
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 12px 18px;
}
.stat-card .num {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.3;
}
.stat-card .num.green {
  color: #67c23a;
}
.stat-card .num.red {
  color: #f56c6c;
}
.stat-card .num.orange {
  color: #e6a23c;
}
.stat-card .num.gray {
  color: #909399;
}
.stat-card .num.blue {
  color: #409eff;
}
.stat-card .label {
  color: #909399;
  font-size: 12px;
}
.case-name {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  margin-right: 10px;
}
.case-module {
  color: #909399;
  font-size: 12px;
  font-weight: 400;
  margin-left: 8px;
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
/* 套件与用例使用轻量级 disclosure，不触发 Element Plus 的深监听和高度测量。 */
.report-tree {
  border: none;
}
.log-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.log-toolbar h2 {
  margin: 0;
}
.log-load-more {
  display: flex;
  justify-content: center;
  margin: 8px 0;
}
.suite-card {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  margin-bottom: 14px;
  overflow: hidden;
}
.disclosure-header {
  width: 100%;
  border: 0;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.suite-header {
  display: flex;
  align-items: center;
  min-height: 48px;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid transparent;
}
.suite-header.is-active {
  border-bottom-color: var(--el-border-color-lighter);
}
.suite-header:hover {
  background: var(--el-fill-color-light);
}
.suite-header:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}
.disclosure-caret {
  flex: none;
  margin-right: 8px;
  color: var(--el-text-color-secondary);
  transition: transform 0.12s ease;
}
.disclosure-caret.is-open {
  transform: rotate(90deg);
}
.suite-body {
  padding: 12px 16px;
}
.suite-phase {
  color: #909399;
  font-size: 12px;
  font-weight: 600;
  margin: 8px 0 4px;
}
.error-box {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #f56c6c;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  margin: 8px 0;
}
.truncate-note {
  color: #e6a23c;
  margin-bottom: 8px;
}
.mt8 {
  margin-top: 8px;
}
.mr8 {
  margin-right: 8px;
}
.exclusion-collapse {
  margin-top: 4px;
}
.exclusion-sub {
  border: none;
}
.exclusion-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}
.exclusion-path {
  font-size: 13px;
}
.exclusion-detail {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
