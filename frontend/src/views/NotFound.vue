<script setup lang="ts">
// V2 404 页：F11 完善
</script>

<template>
  <div class="status-page">
    <div class="v2-page-title">页面或数据不存在</div>
    <p class="v2-aux">请检查地址是否正确。</p>
    <el-button type="primary" @click="$router.push('/dashboard')">返回工作台</el-button>
  </div>
</template>

<style scoped>
.status-page {
  padding: 80px 24px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}
</style>