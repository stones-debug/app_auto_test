<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { createRelease, deleteRelease, listReleases, type AppProfileRelease } from '@/api/appProfiles'
import { ElMessage, ElMessageBox } from 'element-plus'

const props = defineProps<{ profileId: number; revision: number }>()
const visible = defineModel<boolean>('modelValue')

const releases = ref<AppProfileRelease[]>([])
const loading = ref(false)
const form = ref({ version: '', build_number: '', description: '' })

async function load() {
  loading.value = true
  try {
    const page = await listReleases(props.profileId, { status: 'all', page_size: 100 })
    releases.value = page.items
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  if (!form.value.version.trim()) {
    ElMessage.warning('请输入版本号')
    return
  }
  try {
    await createRelease(props.profileId, form.value)
    ElMessage.success('已创建版本')
    form.value = { version: '', build_number: '', description: '' }
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function onDelete(r: any) {
  await ElMessageBox.confirm(`确认删除版本「${r.version}」？`, '提示', { type: 'warning' })
  await deleteRelease(r.id, {})
  ElMessage.success('已删除')
  await load()
}

function createDefaultKey(): string {
  return `${new Date().getFullYear()}0${new Date().getMonth() + 1}.01`
}

onMounted(load)
</script>

<template>
  <el-dialog v-model="visible" title="发布版本管理" width="560px">
    <el-table :data="releases" v-loading="loading" size="small" max-height="320">
      <el-table-column prop="version" label="版本" min-width="120" />
      <el-table-column prop="build_number" label="构建号" min-width="120">
        <template #default="{ row }">{{ row.build_number ?? '-' }}</template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="80" />
      <el-table-column label="操作" width="80" align="right">
        <template #default="{ row }">
          <el-button size="small" text type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-divider />
    <el-form :inline="true" size="small">
      <el-form-item label="版本号">
        <el-input v-model="form.version" placeholder="如 2026.08.1" style="width: 120px" />
      </el-form-item>
      <el-form-item label="构建号">
        <el-input v-model="form.build_number" placeholder="可选" style="width: 120px" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onCreate">新增</el-button>
        <el-button @click="form.version = createDefaultKey()">用日期</el-button>
      </el-form-item>
    </el-form>
  </el-dialog>
</template>
