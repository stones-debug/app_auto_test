<script setup lang="ts">
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
if (!auth.user) {
  auth.fetchMe().catch(() => {})
}
</script>

<template>
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>
