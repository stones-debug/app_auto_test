<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const projectId = computed(() => Number(route.params.projectId))

const tabs = [
  { name: 'Cases', path: `/projects/${projectId.value}/cases`, label: '用例管理' },
  { name: 'Elements', path: `/projects/${projectId.value}/elements`, label: '元素管理' },
  { name: 'Suites', path: `/projects/${projectId.value}/suites`, label: '套件管理' },
  { name: 'Variables', path: `/projects/${projectId.value}/variables`, label: '变量管理' },
]
</script>

<template>
  <div>
    <el-tabs :model-value="route.name" class="project-tabs" @tab-change="(n: string) => $router.push(tabs.find((t) => t.name === n)!.path)">
      <el-tab-pane v-for="tab in tabs" :key="tab.name" :label="tab.label" :name="tab.name" />
    </el-tabs>
    <router-view />
  </div>
</template>

<style scoped>
.project-tabs {
  margin-bottom: 8px;
}
</style>