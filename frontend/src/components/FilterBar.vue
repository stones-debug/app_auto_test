<script setup lang="ts">
// V2 §3.5 / §4.1：筛选栏（具名插槽放置筛选控件；左侧自动排布）
defineProps<{ resultCount?: number }>()
</script>

<template>
  <div class="filter-bar toolbar-card">
    <div class="filters">
      <slot />
    </div>
    <div class="spacer" />
    <div v-if="resultCount !== undefined" class="result-count v2-aux">{{ resultCount }} 条</div>
    <el-button @click="$emit('reset')">清空</el-button>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.result-count {
  white-space: nowrap;
}
</style>