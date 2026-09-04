<script setup lang="ts">
import type { ReportNode } from '@/api/reports'
import { reportFileUrl } from '@/api/reports'
import AuthenticatedImage from '@/components/AuthenticatedImage.vue'
import { formatParameters } from '@/utils/parameters'

defineProps<{ nodes: ReportNode[]; reportId: number }>()

function durationText(ms: number | null | undefined) {
  if (ms == null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}
</script>

<template>
  <el-table :data="nodes" size="small">
    <el-table-column prop="node_order" label="#" width="55" />
    <el-table-column label="类型" width="90"><template #default="{ row }">{{ row.kind === 'action' ? '操作' : '断言' }}</template></el-table-column>
    <el-table-column label="节点" width="150"><template #default="{ row }">{{ row.kind === 'action' ? row.action : row.assertion_type }}</template></el-table-column>
    <el-table-column label="参数" min-width="180" show-overflow-tooltip><template #default="{ row }">{{ formatParameters(row.parameters) || '-' }}</template></el-table-column>
    <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.status === 'passed' ? 'success' : row.status === 'pending' ? 'info' : 'danger'" size="small">{{ row.status }}</el-tag></template></el-table-column>
    <el-table-column label="尝试" width="70"><template #default="{ row }">{{ row.attempt_count ?? 0 }}</template></el-table-column>
    <el-table-column label="耗时" width="90"><template #default="{ row }">{{ durationText(row.duration) }}</template></el-table-column>
    <el-table-column prop="expected_value" label="期望" min-width="120" show-overflow-tooltip />
    <el-table-column prop="actual_value" label="实际" min-width="120" show-overflow-tooltip />
    <el-table-column prop="error_message" label="错误" min-width="160" show-overflow-tooltip />
    <el-table-column label="截图" width="130">
      <template #default="{ row }">
        <AuthenticatedImage
          v-if="row.screenshot"
          :src="reportFileUrl(reportId, row.screenshot)"
          :preview="true"
          alt="节点截图"
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
