<script setup lang="ts">
// V2 §3.5：错误状态（摘要 + 重试 + 帮助入口）
withDefaults(
  defineProps<{ title?: string; description?: string }>(),
  { title: '加载失败', description: '请稍后重试，或检查网络连接。' },
)

defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="error-state">
    <div class="error-icon">⚠️</div>
    <div class="error-title v2-card-title">{{ title }}</div>
    <div class="error-desc v2-aux">{{ description }}</div>
    <div class="error-actions">
      <el-button type="primary" @click="$emit('retry')">重试</el-button>
    </div>
  </div>
</template>

<style scoped>
.error-state {
  padding: 56px 24px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.error-icon {
  font-size: 32px;
}
.error-desc {
  max-width: 400px;
}
.error-actions {
  margin-top: 8px;
}
</style>