<script setup lang="ts">
// V2 §2.3 / §3.5：页面头部（面包屑 + 标题 + 说明 + 主操作）
withDefaults(
  defineProps<{
    title: string
    description?: string
    crumbs?: { text: string; to?: string }[]
  }>(),
  { description: '', crumbs: () => [] },
)
</script>

<template>
  <div class="page-header">
    <div class="header-left">
      <el-breadcrumb v-if="crumbs.length" separator="/" class="crumbs">
        <el-breadcrumb-item v-for="(c, i) in crumbs" :key="i" :to="c.to ? { path: c.to } : undefined">
          {{ c.text }}
        </el-breadcrumb-item>
      </el-breadcrumb>
      <div class="v2-page-title">{{ title }}</div>
      <div v-if="description" class="v2-aux">{{ description }}</div>
    </div>
    <div class="header-actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.header-left {
  min-width: 0;
}
.crumbs {
  margin-bottom: 6px;
}
.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>