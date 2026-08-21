<script setup lang="ts">
// V2 无权限页：F11 完善
</script>

<template>
  <div class="status-page">
    <div class="v2-page-title">没有权限访问此内容</div>
    <p class="v2-aux">请联系项目 owner/admin 获取访问权限。</p>
    <el-button type="primary" @click="$router.push('/dashboard')">返回工作台</el-button>
    <el-button @click="$router.back()">返回上一页</el-button>
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