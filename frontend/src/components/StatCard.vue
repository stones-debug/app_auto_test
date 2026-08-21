<script setup lang="ts">
// V2 §3.5 / §5.2：KPI 统计卡片（数值 + 标签 + 可选辅助说明 + 点击跳转）
withDefaults(
  defineProps<{
    label: string
    value: string | number
    aux?: string
    tone?: 'primary' | 'success' | 'warning' | 'danger'
  }>(),
  { aux: '', tone: 'primary' },
)

defineEmits<{ click: [] }>()
</script>

<template>
  <div class="stat-card" @click="$emit('click')">
    <div class="stat-label v2-aux">{{ label }}</div>
    <div class="stat-value v2-kpi" :class="`tone-${tone}`">{{ value }}</div>
    <div v-if="aux" class="stat-aux v2-aux">{{ aux }}</div>
  </div>
</template>

<style scoped>
.stat-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 16px 20px;
  cursor: pointer;
  transition: box-shadow 0.15s;
}
.stat-card:hover {
  box-shadow: 0 2px 8px rgba(30, 41, 59, 0.08);
}
.stat-label {
  color: var(--text-2);
}
.stat-value {
  margin: 4px 0 2px;
}
.tone-primary {
  color: var(--primary);
}
.tone-success {
  color: var(--success);
}
.tone-warning {
  color: var(--warning);
}
.tone-danger {
  color: var(--danger);
}
.stat-aux {
  color: var(--text-2);
}
</style>