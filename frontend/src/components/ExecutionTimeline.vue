<script setup lang="ts">
// V2 §5.14：执行时间线——套件/用例/步骤/断言实时状态（幂等合并 case_id+step_order）
// 方案 §4.3：嵌套 suites（套件卡片 = 套件头 + 套件前置 + 套件内用例 + 套件后置）
import { computed } from 'vue'

import { useRoute } from 'vue-router'

import AuthenticatedImage from '@/components/AuthenticatedImage.vue'
import { assertionPassed, orderExecutionItems, type OrderedExecutionItem } from '@/utils/executionOrder'
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
  step_order: number
  action: string
  phase?: TimelineStepPhase
  parameters: Record<string, unknown>
  status: string
  duration?: number | null
  actual_value?: string | null
  error_message?: string | null
  artifact_id?: number | null
}

export interface TimelineAssertion {
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
  case_id: number
  case_name: string
  status: string
  steps: TimelineStep[]
  assertions: TimelineAssertion[]
}

export interface TimelineSuite {
  suite_id: number | null
  suite_name: string
  status: string
  duration?: number | null
  error_message?: string | null
  setup_steps: TimelineStep[]
  cases: TimelineCase[]
  teardown_steps: TimelineStep[]
}

// 模板中使用 suites（v-for），props 无需在脚本中显式引用
defineProps<{ suites: TimelineSuite[] }>()
const route = useRoute()
const executionId = computed(() => Number(route.params.executionId))

function artifactUrl(artifactId: number): string {
  return `/api/executions/${executionId.value}/artifacts/${artifactId}`
}

function executionItems(c: TimelineCase) {
  return orderExecutionItems(c.steps, c.assertions)
}

// 套件前后置步骤：纯步骤（无断言），按 step_order 顺序渲染
function suiteItems(steps: TimelineStep[]): OrderedExecutionItem<TimelineStep, never>[] {
  return steps.map((step, index) => ({ kind: 'step' as const, index, value: step }))
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
    <div v-for="s in suites" :key="s.suite_id ?? s.suite_name" class="suite-block">
      <div class="suite-head">
        <span class="suite-name v2-card-title">{{ s.suite_name }}</span>
        <StatusBadge :status="s.status" />
        <span v-if="s.duration != null" class="suite-dur v2-aux">{{ fmtDuration(s.duration) }}</span>
      </div>
      <div v-if="s.error_message" class="suite-error v2-aux">{{ s.error_message }}</div>

      <div v-if="s.setup_steps.length" class="phase-group">
        <div class="phase-label v2-aux">套件前置</div>
        <div v-for="item in suiteItems(s.setup_steps)" :key="`su-${s.suite_id}-${item.index}`" class="step-row">
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
      </div>

      <div v-for="c in s.cases" :key="c.case_id" class="case-block">
        <div class="case-head">
          <span class="case-name v2-card-title">{{ c.case_name }}</span>
          <StatusBadge :status="c.status" />
        </div>
        <template v-for="item in executionItems(c)" :key="`${c.case_id}-${item.kind}-${item.index}`">
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
      </div>

      <div v-if="s.teardown_steps.length" class="phase-group">
        <div class="phase-label v2-aux">套件后置</div>
        <div v-for="item in suiteItems(s.teardown_steps)" :key="`st-${s.suite_id}-${item.index}`" class="step-row">
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
      </div>
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
.suite-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}
.suite-name {
  flex: 1;
}
.suite-dur {
  font-size: 12px;
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
  display: flex;
  align-items: center;
  justify-content: space-between;
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
