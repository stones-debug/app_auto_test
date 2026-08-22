<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useLayoutStore } from '@/stores/layout'
import { useProjectContextStore } from '@/stores/projectContext'

const route = useRoute()
const layout = useLayoutStore()
const projectContext = useProjectContextStore()
const projectId = computed(() => Number(route.params.projectId))

watch(
  projectId,
  (id) => {
    if (!Number.isInteger(id) || id <= 0) return
    layout.visitProject(id)
    void projectContext.load(id).catch(() => {
      // 页面自身接口仍负责呈现 403/404；侧栏保留项目 ID 占位，避免未处理 Promise。
    })
  },
  { immediate: true },
)
</script>

<template>
  <router-view />
</template>
