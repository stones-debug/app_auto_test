<script setup lang="ts">
import { computed } from 'vue'
import { executionStatusMeta } from '@/api/executions'

// V2 §3.4：状态组件必须输出文本和图标，不能只改变颜色
const props = withDefaults(
  defineProps<{ status: string; kind?: 'execution' | 'agent' | 'device' }>(),
  { kind: 'execution' },
)

const STATUS_ICONS: Record<string, string> = {
  running: '●',
  stopping: '◐',
  queued: '○',
  passed: '✓',
  failed: '✕',
  error: '✕',
  stopped: '—',
  cancelled: '—',
}

const META: Record<string, { label: string; type: 'primary' | 'success' | 'warning' | 'danger' | 'info' }> = {
  execution_queued: { label: '排队中', type: 'info' },
  execution_running: { label: '运行中', type: 'primary' },
  execution_stopping: { label: '停止中', type: 'warning' },
  execution_passed: { label: '通过', type: 'success' },
  execution_failed: { label: '失败', type: 'danger' },
  execution_error: { label: '异常', type: 'danger' },
  execution_stopped: { label: '已停止', type: 'info' },
  execution_cancelled: { label: '已取消', type: 'info' },
  agent_online: { label: '在线', type: 'success' },
  agent_offline: { label: '离线', type: 'info' },
  agent_busy: { label: '忙碌', type: 'warning' },
  device_idle: { label: '空闲', type: 'success' },
  device_busy: { label: '忙碌', type: 'warning' },
  device_offline: { label: '离线', type: 'info' },
  device_error: { label: '异常', type: 'danger' },
}

const meta = computed(() => {
  if (props.kind === 'execution') return executionStatusMeta(props.status)
  const fallback = META[`${props.kind}_${props.status}`] ?? { label: props.status, type: 'info' as const }
  return { label: fallback.label, type: fallback.type }
})

const icon = computed(() => STATUS_ICONS[props.status] ?? '')
</script>

<template>
  <el-tag :type="meta.type" size="small" effect="light">
    <span class="status-icon" :class="status">{{ icon }}</span>
    {{ meta.label }}
  </el-tag>
</template>

<style scoped>
.status-icon {
  margin-right: 4px;
  font-size: 12px;
}
</style>