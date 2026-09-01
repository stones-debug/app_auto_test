<script setup lang="ts">
import { executionStatusMeta } from '@/api/executions'
import ReportStepTable from '@/components/ReportStepTable.vue'
import ReportNodeTable from '@/components/ReportNodeTable.vue'
import type { PreparedReportCase } from '@/utils/reportDisclosure'

defineProps<{
  caseItem: PreparedReportCase
  reportId: number
  expanded: boolean
}>()

const emit = defineEmits<{
  toggle: []
}>()

function durationText(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function statusMeta(status: string) {
  return executionStatusMeta(status)
}
</script>

<template>
  <article class="case-card">
    <button
      type="button"
      class="disclosure-header case-header"
      :class="{ 'is-active': expanded }"
      :aria-expanded="expanded"
      @click="emit('toggle')"
    >
      <span class="disclosure-caret" :class="{ 'is-open': expanded }" aria-hidden="true">▸</span>
      <span class="case-name">
        {{ caseItem.case_name }}
        <span v-if="caseItem.module_name" class="case-module">{{ caseItem.module_name }}</span>
      </span>
      <el-tag :type="statusMeta(caseItem.status).type" size="small">
        {{ statusMeta(caseItem.status).label }}
      </el-tag>
      <span class="case-dur">{{ durationText(caseItem.duration) }}</span>
    </button>

    <div v-if="expanded" class="case-body">
      <div v-if="caseItem.error_message" class="error-box">{{ caseItem.error_message }}</div>
      <ReportNodeTable v-if="caseItem.nodes?.length" :nodes="caseItem.nodes" />
      <ReportStepTable
        v-else-if="caseItem.steps.length"
        :steps="caseItem.steps"
        :report-id="reportId"
      />
      <el-empty v-else description="无步骤" :image-size="60" />
    </div>
  </article>
</template>

<style scoped>
.case-card {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  margin-bottom: 10px;
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
.case-header {
  display: flex;
  align-items: center;
  min-height: 42px;
  padding: 10px 16px;
  background: #fff;
  border-bottom: 1px solid transparent;
}
.case-header.is-active {
  border-bottom-color: var(--el-border-color-lighter);
}
.case-header:hover {
  background: var(--el-fill-color-light);
}
.case-header:focus-visible {
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
.case-name {
  flex: 1;
  min-width: 0;
  margin-right: 10px;
  font-weight: 600;
}
.case-module {
  margin-left: 8px;
  color: #909399;
  font-size: 12px;
  font-weight: 400;
}
.case-dur {
  margin-left: 10px;
  color: #999;
  font-size: 12px;
}
.case-body {
  padding: 12px 16px;
}
.error-box {
  margin: 8px 0;
  padding: 8px 12px;
  border: 1px solid #fecaca;
  border-radius: 6px;
  background: #fef2f2;
  color: #f56c6c;
  font-size: 13px;
}
.mt8 {
  margin-top: 8px;
}
</style>
