<script setup lang="ts">
import type { ReportStep } from '@/api/reports'
import { reportFileUrl } from '@/api/reports'
import AuthenticatedImage from '@/components/AuthenticatedImage.vue'
import { formatParameters } from '@/utils/parameters'

defineProps<{
  steps: ReportStep[]
  reportId: number
}>()

function durationText(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

// 五值 phase 归一化为中文标签（未匹配时兜底为"主体"）
function phaseLabel(phase?: string) {
  switch (phase) {
    case 'setup':
    case 'case_setup':
      return '用例前置'
    case 'teardown':
    case 'case_teardown':
      return '用例后置'
    case 'suite_setup':
      return '套件前置'
    case 'suite_teardown':
      return '套件后置'
    default:
      return '主体'
  }
}
</script>

<template>
  <el-table :data="steps" size="small">
    <el-table-column prop="step_order" label="#" width="50" />
    <el-table-column label="阶段" width="80">
      <template #default="{ row }">{{ phaseLabel(row.phase) }}</template>
    </el-table-column>
    <el-table-column prop="action" label="动作" width="120" />
    <el-table-column label="参数" min-width="180" show-overflow-tooltip>
      <template #default="{ row }">{{ formatParameters(row.parameters) || '-' }}</template>
    </el-table-column>
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
</template>

<style scoped>
.thumb {
  width: 60px;
  height: 80px;
  border-radius: 4px;
  border: 1px solid #eee;
}
</style>
