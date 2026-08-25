<script setup lang="ts">
// 方案 §5.2：统一状态标签——正常/直接跳过/继承跳过/已覆盖。
const props = defineProps<{
  effectiveStatus: string
  statusSource?: string
}>()

function label(): string {
  if (props.effectiveStatus === 'skipped') {
    return props.statusSource === 'direct' ? '已跳过' : '继承跳过'
  }
  if (props.effectiveStatus === 'overridden') return '已覆盖'
  return '正常'
}

function type(): 'success' | 'danger' | 'warning' | 'info' | 'primary' {
  if (props.effectiveStatus === 'skipped') return 'danger'
  if (props.effectiveStatus === 'overridden') return 'primary'
  return 'success'
}
</script>

<template>
  <el-tag :type="type()" size="small">{{ label() }}</el-tag>
</template>
