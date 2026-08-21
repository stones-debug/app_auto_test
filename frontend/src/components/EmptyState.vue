<script setup lang="ts">
// V2 §3.5：空状态（首次使用引导 + 可执行主操作）
withDefaults(
  defineProps<{ title: string; description?: string; actionLabel?: string }>(),
  { description: '', actionLabel: '' },
)

defineEmits<{ action: [] }>()
</script>

<template>
  <div class="empty-state">
    <div class="empty-icon">📭</div>
    <div class="empty-title v2-card-title">{{ title }}</div>
    <div v-if="description" class="empty-desc v2-aux">{{ description }}</div>
    <el-button v-if="actionLabel" type="primary" class="empty-action" @click="$emit('action')">
      {{ actionLabel }}
    </el-button>
  </div>
</template>

<style scoped>
.empty-state {
  padding: 56px 24px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.empty-icon {
  font-size: 32px;
}
.empty-title {
  color: var(--text);
}
.empty-desc {
  max-width: 360px;
}
.empty-action {
  margin-top: 8px;
}
</style>