<script setup lang="ts">
import { onMounted } from 'vue'

import { createAppProfile, type AppProfileSummary } from '@/api/appProfiles'
import { useAppProfileStore } from '@/stores/appProfile'
import { ElMessage } from 'element-plus'

const props = defineProps<{ projectId: number }>()
const store = useAppProfileStore()

async function load() {
  store.projectId = props.projectId
  await store.loadProfiles()
}

function select(p: AppProfileSummary) {
  store.selectProfile(p.id)
}

async function onCreate() {
  const { value } = await ElMessageBox.prompt('请输入档案名称', '新建 APP 档案', {
    inputValue: '',
    inputValidator: (v) => (v && v.trim() ? true : '名称不能为空'),
  })
  const code = promptCode(value)
  try {
    await createAppProfile(props.projectId, { name: value.trim(), code })
    ElMessage.success('已创建')
    await load()
    const created = store.profiles[store.profiles.length - 1]
    if (created) store.selectProfile(created.id)
  } catch (e) {
    ElMessage.error(`创建失败：${(e as Error).message}`)
  }
}

function promptCode(name: string): string {
  const base = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 60) || 'profile'
  return `${base}-${Date.now().toString(36).slice(-4)}`
}

function skipLabel(p: AppProfileSummary): string {
  const c = p.skip_counts
  return `${c.case} 用例 / ${c.step} 步骤`
}

onMounted(load)
</script>

<template>
  <div class="profile-tree">
    <div class="tree-head">
      <div class="tree-title">APP 配置档案</div>
      <el-button size="small" type="primary" text @click="onCreate">+ 新建</el-button>
    </div>
    <el-tag v-if="store.stale" type="warning" size="small" class="stale-tag">配置已更新，请刷新</el-tag>
    <div
      class="tree-item"
      :class="{ active: store.selectedProfileId === p.id }"
      v-for="p in store.profiles"
      :key="p.id"
      @click="select(p)"
    >
      <div class="tree-name">{{ p.name }}</div>
      <div class="tree-meta">
        <span>rev {{ p.revision }}</span>
        <span class="diff">{{ skipLabel(p) }}</span>
      </div>
    </div>
    <el-empty v-if="store.profiles.length === 0" description="暂无档案" :image-size="60" />
  </div>
</template>

<style scoped>
.profile-tree {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tree-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
}
.tree-title {
  font-weight: 600;
  font-size: 13px;
}
.stale-tag {
  margin: 0 10px;
}
.tree-item {
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
}
.tree-item:hover {
  background: var(--el-fill-color-light);
}
.tree-item.active {
  background: var(--el-color-primary-light-9);
}
.tree-name {
  font-size: 13px;
  font-weight: 500;
}
.tree-meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  display: flex;
  gap: 8px;
}
.diff {
  color: var(--el-color-danger);
}
</style>
