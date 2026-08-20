<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ElMessage, ElMessageBox } from 'element-plus'

import { CASE_STATUS, cloneCase, deleteCase, listCases, type TestCase } from '@/api/cases'
import { listModules } from '@/api/elements'
import RunDialog from '@/components/RunDialog.vue'

const route = useRoute()
const router = useRouter()
const projectId = Number(route.params.projectId)

const loading = ref(false)
const items = ref<TestCase[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const statusFilter = ref('')
const modules = ref<{ id: number; name: string }[]>([])
const moduleId = ref<number | null>(null)
const runDialog = ref<{ open: () => void } | null>(null)
const runningCase = ref<TestCase | null>(null)

async function loadModules() {
  modules.value = await listModules(projectId)
}

function openRun(row: TestCase) {
  runningCase.value = row
  runDialog.value?.open()
}

async function load() {
  loading.value = true
  try {
    const data = await listCases(projectId, {
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value,
      status: statusFilter.value,
      module_id: moduleId.value,
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function openCreate() {
  router.push(`/projects/${projectId}/cases/new`)
}

function openEdit(row: TestCase) {
  router.push(`/projects/${projectId}/cases/${row.id}/edit`)
}

async function remove(row: TestCase) {
  await ElMessageBox.confirm(`确认删除用例「${row.name}」？`, '提示', { type: 'warning' })
  await deleteCase(row.id)
  ElMessage.success('已删除')
  await load()
}

async function clone(row: TestCase) {
  await cloneCase(row.id)
  ElMessage.success('已克隆')
  await load()
}

function statusTag(s: string) {
  return CASE_STATUS.find((x) => x.value === s)?.label ?? s
}

function statusType(s: string) {
  return s === 'active' ? 'success' : s === 'disabled' ? 'info' : 'warning'
}

onMounted(() => {
  loadModules()
  load()
})
</script>

<template>
  <div>
    <div class="toolbar">
      <el-select v-model="moduleId" placeholder="按模块筛选" clearable class="module" @change="page = 1; load()">
        <el-option v-for="m in modules" :key="m.id" :label="m.name" :value="m.id" />
      </el-select>
      <el-input v-model="keyword" placeholder="按名称搜索" clearable class="search" @keyup.enter="page = 1; load()" />
      <el-select v-model="statusFilter" placeholder="状态" clearable class="status" @change="page = 1; load()">
        <el-option v-for="s in CASE_STATUS" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-button type="primary" @click="page = 1; load()">搜索</el-button>
      <el-button type="primary" plain @click="openCreate">新建用例</el-button>
    </div>

    <el-table v-loading="loading" :data="items">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
      <el-table-column prop="module_name" label="模块" width="130" />
      <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusTag(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="180" />
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="success" text @click="openRun(row)">运行</el-button>
          <el-button size="small" type="primary" text @click="openEdit(row)">编辑</el-button>
          <el-button size="small" text @click="clone(row)">克隆</el-button>
          <el-button size="small" type="danger" text @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <RunDialog
      v-if="runningCase"
      ref="runDialog"
      :type="'case'"
      :id="runningCase.id"
      :name="runningCase.name"
    />

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      class="pager"
      @change="load"
    />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.module {
  width: 150px;
}
.search {
  width: 200px;
}
.status {
  width: 110px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>