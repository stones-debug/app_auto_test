<script setup lang="ts">
// V2 §5.14：执行时间线——用例、步骤、断言实时状态（幂等合并 case_id+step_order）
// Step 7：有 artifact_id 的步骤展示鉴权截图入口（AuthenticatedImage Blob 加载）
import { computed } from 'vue'

import { useRoute } from 'vue-router'

import AuthenticatedImage from '@/components/AuthenticatedImage.vue'
import { assertionPassed, orderExecutionItems } from '@/utils/executionOrder'
import { formatParameters } from '@/utils/parameters'

export interface TimelineStep {
  step_order: number
  action: string
  phase?: 'setup' | 'main' | 'teardown'
  parameters: Record<string, unknown>
  status: string
  duration?: number | null
  actual_value?: string | null
  error_message?: string | null
  artifact_id?: number | null
}

export interface TimelineAssertion {
  assertion_type: string
  expected_value?: string | null
  actual_value?: string | null
  status: string
  error_message?: string | null
}

export interface TimelineCase {
  case_id: number
  case_name: string
  status: string
  steps: TimelineStep[]
  assertions: TimelineAssertion[]
}

// 模板中使用 cases（v-for），props 无需在脚本中显式引用
defineProps<{ cases: TimelineCase[] }>()
const route = useRoute()
const executionId = computed(() => Number(route.params.executionId))

function artifactUrl(artifactId: number): string {
  return `/api/executions/${executionId.value}/artifacts/${artifactId}`
}

function executionItems(c: TimelineCase) {
  return orderExecutionItems(c.steps, c.assertions)
}
</script>

<template>
  <div class="exec-timeline">
    <div v-for="c in cases" :key="c.case_id" class="case-block">
      <div class="case-head">
        <span class="case-name v2-card-title">{{ c.case_name }}</span>
        <StatusBadge :status="c.status" />
      </div>
      <template v-for="item in executionItems(c)" :key="`${c.case_id}-${item.kind}-${item.index}`">
        <div v-if="item.kind === 'step'" class="step-row">
          <span class="step-icon" :class="item.value.status">{{ item.value.status === 'passed' ? '✓' : item.value.status === 'failed' ? '✕' : '○' }}</span>
          <span class="step-order">#{{ item.value.step_order }}</span>
          <el-tag v-if="item.value.phase && item.value.phase !== 'main'" size="small" :type="item.value.phase === 'setup' ? 'warning' : 'success'">
            {{ item.value.phase === 'setup' ? '前置' : '后置' }}
          </el-tag>
          <span class="step-action">{{ item.value.action }}</span>
          <span
            v-if="formatParameters(item.value.parameters)"
            class="step-parameters v2-aux"
            :title="formatParameters(item.value.parameters, true)"
          >参数：{{ formatParameters(item.value.parameters) }}</span>
          <span v-if="item.value.status === 'passed' && item.value.duration != null" class="step-duration v2-aux">
            {{ item.value.duration >= 1000 ? `${(item.value.duration / 1000).toFixed(1)}s` : `${item.value.duration}ms` }}
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
            {{ assertionPassed(item.value.status) ? '✓' : '✕' }}
          </span>
          <span class="step-action">{{ item.value.assertion_type }}</span>
          <span v-if="item.value.actual_value != null" class="v2-aux">= {{ item.value.actual_value }}</span>
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
.case-block {
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px 16px;
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
