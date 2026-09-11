<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { CASE_STATUS, cloneCase, deleteCase, deleteCases, listCases, type TestCase } from '@/api/cases'
import ModuleTree from '@/components/ModuleTree.vue'
import RunButton from '@/components/RunButton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { usePermission } from '@/composables/usePermission'
import { formatDateTime } from '@/utils/format'
import {
  CASE_LIST_PAGE_SIZES,
  caseListQuery,
  parseCaseListPage,
  parseCaseListPageSize,
  parseModuleKey,
} from '@/utils/caseModuleNavigation'
import { moduleFilterParams, type ModuleKey } from '@/utils/moduleFilter'

const route = useRoute()
const router = useRouter()
const { canWriteAssets } = usePermission()
const projectId = Number(route.params.projectId)

const loading = ref(false)
const items = ref<TestCase[]>([])
const selectedRows = ref<TestCase[]>([])
const deleting = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const statusFilter = ref('')
const selectedModule = ref<ModuleKey>('all')

function selectModule(key: string, resetPage = true) {
  selectedModule.value = parseModuleKey(key)
  if (resetPage) page.value = 1
  void load()
}

async function load() {
  selectedRows.value = []
  loading.value = true
  try {
    while (true) {
      const data = await listCases(projectId, {
        page: page.value,
        page_size: pageSize.value,
        // 「全部 / 未分组 / 某模块」三态；未分组必须走 ungrouped，不能用 module_id=null
        ...moduleFilterParams(selectedModule.value),
        keyword: keyword.value,
        status: statusFilter.value,
      })
      total.value = data.total
      const lastPage = data.total > 0 ? Math.ceil(data.total / pageSize.value) : 1
      if (page.value !== Math.min(page.value, lastPage)) {
        page.value = Math.min(page.value, lastPage)
        continue
      }
      items.value = data.items
      break
    }
  } finally {
    loading.value = false
  }
}

function openCreate() {
  router.push({
    path: `/projects/${projectId}/cases/new`,
    query: caseListQuery(selectedModule.value, page.value, parseCaseListPageSize(pageSize.value)),
  })
}

function openEdit(row: TestCase) {
  // 编辑页返回时保持当前列表筛选与页码，不切换到用例自身模块。
  router.push({
    path: `/projects/${projectId}/cases/${row.id}/edit`,
    query: caseListQuery(selectedModule.value, page.value, parseCaseListPageSize(pageSize.value)),
  })
}

function onPageChange(nextPage: number) {
  page.value = nextPage
  void load()
}

function onPageSizeChange(nextPageSize: number) {
  pageSize.value = parseCaseListPageSize(nextPageSize)
  page.value = 1
  void load()
}

async function remove(row: TestCase) {
  await ElMessageBox.confirm(`确认删除用例「${row.name}」？`, '提示', { type: 'warning' })
  await deleteCase(row.id)
  ElMessage.success('已删除')
  await load()
}

function handleSelectionChange(rows: TestCase[]) {
  selectedRows.value = rows
}

async function removeSelected() {
  const ids = selectedRows.value.map((row) => row.id)
  if (!ids.length) return
  await ElMessageBox.confirm(`确认删除选中的 ${ids.length} 个用例？`, '提示', { type: 'warning' })
  deleting.value = true
  try {
    const result = await deleteCases(projectId, ids)
    ElMessage.success(`已删除 ${result.deleted ?? ids.length} 个用例`)
    selectedRows.value = []
    await load()
  } finally {
    deleting.value = false
  }
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
  // 编辑页返回会带回模块上下文；没有上下文时才使用“全部”。
  page.value = parseCaseListPage(route.query.page)
  pageSize.value = parseCaseListPageSize(route.query.page_size)
  selectModule(parseModuleKey(route.query.module), false)
})
</script>

<template>
  <div class="cases-layout">
    <!-- 左：模块树（与套件页共用的同一组件） -->
    <ModuleTree :project-id="projectId" scope="case" title="用例模块" :selected-key="selectedModule"
      :writable="canWriteAssets" @select="selectModule" @mutated="load" />

    <!-- 右：列表 -->
    <div class="cases-main">
      <div class="toolbar-card">
        <el-input v-model="keyword" placeholder="按名称搜索" clearable class="search" @keyup.enter="page = 1; load()" />
        <el-select v-model="statusFilter" placeholder="状态" clearable class="status" @change="page = 1; load()">
          <el-option v-for="s in CASE_STATUS" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
        <el-button type="primary" @click="page = 1; load()">搜索</el-button>
        <span class="spacer"></span>
        <el-button v-if="canWriteAssets" type="danger" plain :disabled="!selectedRows.length || deleting"
          :loading="deleting" @click="removeSelected">批量删除</el-button>
        <el-button v-if="canWriteAssets" type="primary" @click="openCreate">新建用例</el-button>
      </div>

      <div v-if="selectedRows.length" class="batch-bar">
        <span>已选 {{ selectedRows.length }} 个用例</span>
      </div>

      <el-table v-loading="loading" :data="items" stripe row-key="id" @selection-change="handleSelectionChange">
        <el-table-column v-if="canWriteAssets" type="selection" width="42" />
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="case-name" :class="{ clickable: canWriteAssets }"
              @click="canWriteAssets && openEdit(row as TestCase)">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="module_name" label="模块" width="120" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : row.status === 'disabled' ? 'danger' : 'warning'"
              size="small">
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
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <RunButton v-if="(row as TestCase).status !== 'disabled'" :type="'case'" :id="(row as TestCase).id"
              :name="(row as TestCase).name" />
            <template v-if="canWriteAssets">
              <el-button size="small" type="primary" text @click="openEdit(row as TestCase)">编辑</el-button>
              <el-button size="small" text @click="clone(row as TestCase)">克隆</el-button>
              <el-button size="small" type="danger" text @click="remove(row as TestCase)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination :current-page="page" :page-size="pageSize" :total="total" :page-sizes="[...CASE_LIST_PAGE_SIZES]"
        layout="total, sizes, prev, pager, next, jumper" class="pager" @current-change="onPageChange"
        @size-change="onPageSizeChange" />
    </div>
  </div>
</template>

<style scoped>
.cases-layout {
  display: flex;
  gap: 16px;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--primary-light);
  border: 1px solid var(--primary);
  border-radius: 6px;
  color: var(--primary);
  font-size: 13px;
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
