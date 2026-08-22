<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { CASE_STATUS, cloneCase, deleteCase, listCases, type TestCase } from '@/api/cases'
import { createModule, listModules } from '@/api/elements'
import RunButton from '@/components/RunButton.vue'
import StatusBadge from '@/components/StatusBadge.vue'

const route = useRoute()
const router = useRouter()
const projectId = Number(route.params.projectId)

const loading = ref(false)
const items = ref<TestCase[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const statusFilter = ref('')
const modules = ref<{ id: number; name: string; parent_id: number | null }[]>([])
const selectedModule = ref<string>('all')

const treeModules = ref<{ id: number; name: string; parent_id: number | null }[]>([])

async function loadModules() {
  modules.value = await listModules(projectId)
  treeModules.value = modules.value
}

const filteredModuleId = ref<number | null>(null)

function selectModule(key: string) {
  selectedModule.value = key
  if (key === 'all') filteredModuleId.value = null
  else if (key === 'none') filteredModuleId.value = -1 // 未分组：后端按 module_id=null 过滤
  else filteredModuleId.value = Number(key)
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const data = await listCases(projectId, {
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value,
      status: statusFilter.value,
      module_id: filteredModuleId.value === -1 ? null : filteredModuleId.value,
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

// 新增模块（模块树下方入口）
const moduleDialogVisible = ref(false)
const newModuleName = ref('')
const moduleCreating = ref(false)

function openCreateModule() {
  newModuleName.value = ''
  moduleDialogVisible.value = true
}

async function submitCreateModule() {
  const name = newModuleName.value.trim()
  if (!name) return
  moduleCreating.value = true
  try {
    const mod = await createModule(projectId, { name })
    moduleDialogVisible.value = false
    ElMessage.success('模块已创建')
    await loadModules()
    selectModule(String(mod.id))
  } finally {
    moduleCreating.value = false
  }
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

function lastExecLabel(status: string | null) {
  return status ?? '未执行'
}

onMounted(() => {
  loadModules()
  load()
})
</script>

<template>
  <div class="cases-layout">
    <!-- 左：模块树 -->
    <div class="module-tree">
      <div class="tree-head v2-card-title">用例模块</div>
      <div class="tree-item" :class="{ active: selectedModule === 'all' }" @click="selectModule('all')">
        全部用例
      </div>
      <div class="tree-item" :class="{ active: selectedModule === 'none' }" @click="selectModule('none')">
        未分组
      </div>
      <div v-for="m in treeModules" :key="m.id" class="tree-item" :class="{ active: selectedModule === String(m.id) }" @click="selectModule(String(m.id))">
        {{ m.name }}
      </div>
      <el-button class="add-module" text type="primary" @click="openCreateModule">+ 新增模块</el-button>
    </div>

    <el-dialog v-model="moduleDialogVisible" title="新增模块" width="420px">
      <el-form label-width="80px" @submit.prevent="submitCreateModule">
        <el-form-item label="模块名称" required>
          <el-input
            v-model="newModuleName"
            placeholder="请输入模块名称"
            maxlength="255"
            @keyup.enter="submitCreateModule"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="moduleDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="moduleCreating" :disabled="!newModuleName.trim()" @click="submitCreateModule">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 右：列表 -->
    <div class="cases-main">
      <div class="toolbar-card">
        <el-input v-model="keyword" placeholder="按名称搜索" clearable class="search" @keyup.enter="page = 1; load()" />
        <el-select v-model="statusFilter" placeholder="状态" clearable class="status" @change="page = 1; load()">
          <el-option v-for="s in CASE_STATUS" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
        <el-button type="primary" @click="page = 1; load()">搜索</el-button>
        <span class="spacer"></span>
        <el-button type="primary" @click="openCreate">新建用例</el-button>
      </div>

      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="case-name" @click="openEdit(row as TestCase)">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="module_name" label="模块" width="120" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : row.status === 'disabled' ? 'danger' : 'warning'" size="small">
              {{ statusTag(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="步骤/断言" width="110">
          <template #default="{ row }">
            {{ row.step_count ?? 0 }} / {{ row.assertion_count ?? 0 }}
          </template>
        </el-table-column>
        <el-table-column label="最近结果" width="120">
          <template #default="{ row }">
            <StatusBadge v-if="row.last_execution_status" :status="row.last_execution_status" />
            <span v-else class="v2-aux">{{ lastExecLabel(row.last_execution_status) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="180" />
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <RunButton v-if="(row as TestCase).status !== 'disabled'" :type="'case'" :id="(row as TestCase).id" :name="(row as TestCase).name" />
            <el-button size="small" type="primary" text @click="openEdit(row as TestCase)">编辑</el-button>
            <el-button size="small" text @click="clone(row as TestCase)">克隆</el-button>
            <el-button size="small" type="danger" text @click="remove(row as TestCase)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        class="pager"
        @change="load"
      />
    </div>
  </div>
</template>

<style scoped>
.cases-layout {
  display: flex;
  gap: 16px;
}
.module-tree {
  width: 220px;
  flex-shrink: 0;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px;
  align-self: flex-start;
}
.tree-head {
  padding: 8px 12px;
}
.tree-item {
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--text-2);
  font-size: 13px;
}
.tree-item:hover {
  background: var(--primary-light);
}
.tree-item.active {
  background: var(--primary-light);
  color: var(--primary);
  font-weight: 600;
}
.add-module {
  width: 100%;
  margin-top: 8px;
  justify-content: center;
  border: 1px dashed var(--border);
  border-radius: 6px;
}
.cases-main {
  flex: 1;
  min-width: 0;
}
.search {
  width: 200px;
}
.status {
  width: 110px;
}
.case-name {
  color: var(--primary);
  cursor: pointer;
}
</style>