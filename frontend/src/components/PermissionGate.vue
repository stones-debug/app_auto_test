<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

// V2 §6.3：按平台/项目角色渲染操作。项目角色从 projectContextStore 读取（F4），
// 平台管理员能力用 authStore.is_admin。未就绪时默认放行（后端为最终防线）。
const props = defineProps<{
  roles?: string[] // 项目角色白名单（owner/admin/member/viewer）
  platformAdmin?: boolean
  fallback?: boolean
}>()

const route = useRoute()
const auth = useAuthStore()

const projectRole = computed(() => {
  // 从 route meta 临时注入（F4 引入 projectContextStore 后改用 store）
  return (route.meta as { projectRole?: string }).projectRole ?? null
})

const allowed = computed(() => {
  if (props.fallback) return true
  if (props.platformAdmin && auth.user?.is_admin) return true
  if (props.roles?.length) {
    return projectRole.value !== null && props.roles.includes(projectRole.value)
  }
  return true
})
</script>

<template>
  <template v-if="allowed"><slot /></template>
</template>