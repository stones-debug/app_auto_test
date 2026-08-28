<script setup lang="ts">
// V2 §5.14：执行时间线——套件/用例/步骤/断言实时状态（幂等合并 case_id+step_order）
// 方案 §4.3：嵌套 suites（套件卡片 = 套件头 + 套件前置 + 套件内用例 + 套件后置）
// 按需展开：套件/用例默认收起，点击头部逐层展开；收起内容用 v-if 真正卸载。
// 步骤/断言行使用稳定 id 作 key（不用数组下标），避免实时 WS 插入/更新时
// keyed patch 误复用组件实例（el-popover/el-button）导致 emitsOptions null 崩溃。
import { computed, ref, watch } from 'vue'

import { useRoute } from 'vue-router'

import AuthenticatedImage from '@/components/AuthenticatedImage.vue'
import { assertionPassed } from '@/utils/executionOrder'
import { formatParameters } from '@/utils/parameters'

export type TimelineStepPhase =
  | 'setup'
  | 'main'
  | 'teardown'
  | 'case_setup'
  | 'case_main'
  | 'case_teardown'
  | 'suite_setup'
  | 'suite_teardown'

export interface TimelineStep {
  id?: number
  step_order: number
  action: string
  phase?: TimelineStepPhase
  parameters: Record<string, unknown>
  status: string
  duration?: number | null
  actual_value?: string | null
  error_message?: string | null
  artifact_id?: number | null
  assertions?: TimelineAssertion[]
}

export interface TimelineAssertion {
  id?: number
  assertion_order: number
  assertion_type: string
  expected_value?: string | null
  actual_value?: string | null
  status: string
  error_message?: string | null
  params?: Record<string, unknown> | null
  description?: string | null
}

export interface TimelineCase {
  id?: number
  case_id: number
  case_name: string
  status: string
  duration?: number | null
  error_message?: string | null
  steps: TimelineStep[]
}

export interface TimelineSuite {
  id?: number
  suite_id: number | null
  suite_name: string
  suite_order?: number
  is_virtual?: boolean
  status: string
  duration?: number | null
  error_message?: string | null
  setup_steps: TimelineStep[]
  cases: TimelineCase[]
  teardown_steps: TimelineStep[]
}

const props = defineProps<{ suites: TimelineSuite[] }>()
const route = useRoute()
const executionId = computed(() => Number(route.params.executionId))

// 展开状态：套件按数组索引、用例按「套件索引:用例索引」记录（执行期间 suites/cases 顺序稳定，索引可作为稳定路径 key）
const expandedSuites = ref<Set<string>>(new Set())
const expandedCases = ref<Set<string>>(new Set())
let seeded = false
let userControlled = false

// 默认展开策略：套件默认收起；仅展开「含失败/error 用例的套件 + 其失败用例」以及「running 状态」的套件，
// running 套件内的 running 用例一并展开以便实时查看当前进度。用户点击后不再自动改状态。
watch(
  () => props.suites,
  (suites) => {
    if (!suites.length || (seeded && userControlled)) return
    const es = new Set<string>()
    const ec = new Set<string>()
    suites.forEach((s, si) => {
      const hasFail = s.cases.some((c) => ['failed', 'error'].includes(c.status))
      const isRunning = s.status === 'running'
      if (isRunning || hasFail) {
        es.add(String(si))
        s.cases.forEach((c, ci) => {
          if (['failed', 'error'].includes(c.status)) ec.add(`${si}:${ci}`)
          else if (isRunning && c.status === 'running') ec.add(`${si}:${ci}`)
        })
      }
    })
    if (!seeded) {
      seeded = true
      expandedSuites.value = es
      expandedCases.value = ec
      return
    }
    // 用户尚未手动控制折叠时，执行开始后自动展开新进入 running/失败的节点。
    expandedSuites.value = new Set([...expandedSuites.value, ...es])
    expandedCases.value = new Set([...expandedCases.value, ...ec])
  },
  { immediate: true },
)

function toggleSuite(si: number) {
  userControlled = true
  const k = String(si)
  const next = new Set(expandedSuites.value)
  if (next.has(k)) next.delete(k)
  else next.add(k)
  expandedSuites.value = next
}

function toggleCase(si: number, ci: number) {
  userControlled = true
  const k = `${si}:${ci}`
  const next = new Set(expandedCases.value)
  if (next.has(k)) next.delete(k)
  else next.add(k)
  expandedCases.value = next
}

function expandAll() {
  userControlled = true
  const es = new Set<string>()
  const ec = new Set<string>()
  props.suites.forEach((s, si) => {
    es.add(String(si))
    s.cases.forEach((_c, ci) => ec.add(`${si}:${ci}`))
  })
  expandedSuites.value = es
  expandedCases.value = ec
}

function collapseAll() {
  userControlled = true
  expandedSuites.value = new Set()
  expandedCases.value = new Set()
}

defineExpose({ expandAll, collapseAll })

function artifactUrl(artifactId: number): string {
  return `/api/executions/${executionId.value}/artifacts/${artifactId}`
}

// 稳定 key：步骤/断言优先用后端 id；缺 id（实时推送尚未回填）时回退 位置+序号。
// 严禁用数组下标作 key——实时更新会让下标位移，导致组件 VNode 被按 index 误复用。
// key 随 item 一并生成（模板只读 item.key），避免模板 :key 中调用 script 函数
// （vue-tsc 对特殊绑定位的函数调用 usage 检测不可靠，会误报 TS6133）。
type KeyedExecutionItem<TStep, TAssertion> =
  | { kind: 'step'; index: number; value: TStep; key: string }
  | { kind: 'assertion'; index: number; value: TAssertion; key: string }

// 套件前后置步骤：纯步骤（无断言），按 step_order 顺序渲染
function suiteItems(s: TimelineSuite, steps: TimelineStep[], prefix: string): KeyedExecutionItem<TimelineStep, never>[] {
  return steps.map((step, index) => ({
    kind: 'step' as const,
    index,
    value: step,
    key: `${prefix}-${s.id ?? s.suite_id ?? 'x'}-${step.id ?? `${step.phase ?? 'main'}-${step.step_order}`}`,
  }))
}

function executionItems(c: TimelineCase): KeyedExecutionItem<TimelineStep, TimelineAssertion>[] {
  return c.steps.flatMap((step, stepIndex) => [
    {
      kind: 'step' as const,
      index: stepIndex,
      value: step,
      key: `step-${step.id ?? `${c.case_id}-${step.phase ?? 'main'}-${step.step_order}`}`,
    },
    ...(step.assertions ?? []).map((assertion, assertionIndex) => ({
      kind: 'assertion' as const,
      index: assertionIndex,
      value: assertion,
      key: `assert-${assertion.id ?? `${step.id ?? step.step_order}-${assertion.assertion_order}`}`,
    })),
  ])
}

function fmtDuration(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

// 阶段标签：五值 phase 归一化为中文短标签（timeout 的 phase 未匹配时兜底）
function phaseLabel(phase?: TimelineStepPhase | string): { text: string; type: 'warning' | 'success' | 'primary' | 'info' } {
  switch (phase) {
    case 'setup':
    case 'case_setup':
      return { text: '用例前置', type: 'warning' }
    case 'teardown':
    case 'case_teardown':
      return { text: '用例后置', type: 'success' }
    case 'suite_setup':
      return { text: '套件前置', type: 'warning' }
    case 'suite_teardown':
      return { text: '套件后置', type: 'success' }
    case 'main':
    case 'case_main':
    default:
      return { text: '主体', type: 'primary' }
  }
}
</script>

<template>
  <div class="exec-timeline">
    <div v-for="(s, si) in suites" :key="s.id ?? s.suite_id ?? si" class="suite-block">
      <div class="suite-head" @click="toggleSuite(si)">
        <span class="caret" :class="{ 'is-open': expandedSuites.has(String(si)) }">▸</span>
        <span class="suite-name v2-card-title">{{ s.suite_name }}</span>
        <span v-if="s.is_virtual" class="v2-aux">虚拟套件</span>
        <StatusBadge :status="s.status" />
        <span v-if="s.duration != null" class="suite-dur v2-aux">{{ fmtDuration(s.duration) }}</span>
      </div>
      <div v-if="s.error_message" class="suite-error v2-aux">{{ s.error_message }}</div>

      <template v-if="expandedSuites.has(String(si))">
        <div v-if="s.setup_steps.length" class="phase-group">
          <div class="phase-label v2-aux">套件前置</div>
          <template v-for="item in suiteItems(s, s.setup_steps, 'su')" :key="item.key">
            <div class="step-row">
              <span class="step-icon" :class="item.value.status">{{ item.value.status === 'passed' ? '✓' : item.value.status === 'failed' ? '✕' : '○' }}</span>
              <span class="step-order">#{{ item.value.step_order }}</span>
              <el-tag v-if="item.value.phase" size="small" :type="phaseLabel(item.value.phase).type">{{ phaseLabel(item.value.phase).text }}</el-tag>
              <span class="step-action">{{ item.value.action }}</span>
              <span
                v-if="formatParameters(item.value.parameters)"
                class="step-parameters v2-aux"
                :title="formatParameters(item.value.parameters, true)"
              >参数：{{ formatParameters(item.value.parameters) }}</span>
              <span v-if="item.value.status === 'passed' && item.value.duration != null" class="step-duration v2-aux">{{ fmtDuration(item.value.duration) }}</span>
              <span v-if="item.value.error_message" class="step-error v2-aux" :title="item.value.error_message">{{ item.value.error_message }}</span>
            </div>
          </template>
        </div>

        <div v-for="(c, ci) in s.cases" :key="c.id ?? c.case_id ?? ci" class="case-block">
          <div class="case-head" @click="toggleCase(si, ci)">
            <span class="caret" :class="{ 'is-open': expandedCases.has(`${si}:${ci}`) }">▸</span>
            <span class="case-name v2-card-title">{{ c.case_name }}</span>
            <StatusBadge :status="c.status" />
          </div>
          <template v-if="expandedCases.has(`${si}:${ci}`)">
            <template v-for="item in executionItems(c)" :key="item.key">
              <div v-if="item.kind === 'step'" class="step-row">
                <span class="step-icon" :class="item.value.status">{{ item.value.status === 'passed' ? '✓' : item.value.status === 'failed' ? '✕' : '○' }}</span>
                <span class="step-order">#{{ item.value.step_order }}</span>
                <el-tag v-if="item.value.phase" size="small" :type="phaseLabel(item.value.phase).type">{{ phaseLabel(item.value.phase).text }}</el-tag>
                <span class="step-action">{{ item.value.action }}</span>
                <span
                  v-if="formatParameters(item.value.parameters)"
                  class="step-parameters v2-aux"
                  :title="formatParameters(item.value.parameters, true)"
                >参数：{{ formatParameters(item.value.parameters) }}</span>
                <span v-if="item.value.status === 'passed' && item.value.duration != null" class="step-duration v2-aux">
                  {{ fmtDuration(item.value.duration) }}
                </span>
                <el-popover v-if="item.value.artifact_id" placement="left" :width="260" trigger="click">
                  <template #reference>
                    <el-button size="small" text type="primary" class="shot-btn">截图</el-button>
                  </template>
                  <AuthenticatedImage :src="artifactUrl(item.value.artifact_id)" alt="步骤截图" />
                </el-popover>
                <span v-if="item.value.error_message" class="step-error v2-aux" :title="item.value.error_message">{{ item.value.error_message }}</span>
              </div>
              <div v-else class="assertion-row">
                <span class="step-icon" :class="assertionPassed(item.value.status) ? 'passed' : 'failed'">
                  {{ assertionPassed(item.value.status) ? '✓' : 'x' }}
                </span>
                <span class="step-order">#{{ item.value.assertion_order }}</span>
                <el-tag size="small" type="primary">断言</el-tag>
                <span class="step-action">{{ item.value.assertion_type }}</span>
                <span
                  v-if="formatParameters(item.value.params)"
                  class="step-parameters v2-aux"
                  :title="formatParameters(item.value.params, true)"
                >参数：{{ formatParameters(item.value.params) }}</span>
                <span v-if="item.value.description" class="assertion-desc v2-aux">{{ item.value.description }}</span>
                <span v-if="item.value.expected_value != null" class="v2-aux">期望：{{ item.value.expected_value }}</span>
                <span v-if="item.value.actual_value != null" class="v2-aux">= {{ item.value.actual_value }}</span>
                <span v-if="item.value.error_message" class="step-error v2-aux" :title="item.value.error_message">{{ item.value.error_message }}</span>
              </div>
            </template>
          </template>
        </div>

        <div v-if="s.teardown_steps.length" class="phase-group">
          <div class="phase-label v2-aux">套件后置</div>
          <template v-for="item in suiteItems(s, s.teardown_steps, 'st')" :key="item.key">
            <div class="step-row">
              <span class="step-icon" :class="item.value.status">{{ item.value.status === 'passed' ? '✓' : item.value.status === 'failed' ? '✕' : '○' }}</span>
              <span class="step-order">#{{ item.value.step_order }}</span>
              <el-tag v-if="item.value.phase" size="small" :type="phaseLabel(item.value.phase).type">{{ phaseLabel(item.value.phase).text }}</el-tag>
              <span class="step-action">{{ item.value.action }}</span>
              <span
                v-if="formatParameters(item.value.parameters)"
                class="step-parameters v2-aux"
                :title="formatParameters(item.value.parameters, true)"
              >参数：{{ formatParameters(item.value.parameters) }}</span>
              <span v-if="item.value.status === 'passed' && item.value.duration != null" class="step-duration v2-aux">{{ fmtDuration(item.value.duration) }}</span>
              <span v-if="item.value.error_message" class="step-error v2-aux" :title="item.value.error_message">{{ item.value.error_message }}</span>
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.exec-timeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.suite-block {
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px 16px;
}
.suite-head,
.case-head {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  user-select: none;
  border-radius: 4px;
  transition: background-color 0.15s ease;
}
.suite-head {
  margin-bottom: 4px;
}
.suite-head:hover,
.case-head:hover {
  background: var(--el-fill-color-light);
}
.suite-name,
.case-head .case-name {
  flex: 1;
}
.case-head .case-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.caret {
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  color: var(--text-2);
  font-size: 12px;
  transition: transform 0.2s ease;
}
.caret.is-open {
  transform: rotate(90deg);
}
.suite-dur {
  font-size: 12px;
}
.suite-name.v2-card-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.suite-error {
  color: var(--danger);
  font-size: 12px;
  margin: 4px 0 8px;
}
.phase-group {
  margin: 6px 0;
}
.phase-label {
  margin: 8px 0 2px;
  font-size: 12px;
}
.case-block {
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px 16px;
  margin-top: 8px;
}
.case-head {
  margin-bottom: 8px;
}
.step-row,
.assertion-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}
.step-icon {
  width: 16px;
  text-align: center;
  font-weight: 700;
}
.step-icon.passed {
  color: var(--success);
}
.step-icon.failed {
  color: var(--danger);
}
.step-order {
  color: var(--text-2);
  font-size: 12px;
  width: 28px;
  flex-shrink: 0;
}
.step-action {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.step-parameters {
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.assertion-row {
  flex-wrap: wrap;
  column-gap: 8px;
}
.assertion-desc {
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 30%;
}
.step-duration {
  margin-left: auto;
}
.step-error {
  color: var(--danger);
  margin-left: auto;
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.shot-btn {
  margin-left: 4px;
  flex-shrink: 0;
}
</style>
