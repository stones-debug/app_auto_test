<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Delete, Edit, Folder, Plus } from '@element-plus/icons-vue'

import { CASE_STATUS, cloneCase, deleteCase, deleteCases, listCases, type TestCase } from '@/api/cases'
import { createModule, deleteModule, listModules, updateModule, type TestModule } from '@/api/elements'
import RunButton from '@/components/RunButton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { usePermission } from '@/composables/usePermission'
import { formatDateTime } from '@/utils/format'

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
const modules = ref<TestModule[]>([])
const selectedModule = ref<string>('all')
const collapsedModules = ref<Set<number>>(new Set())

type ModuleTreeNode = TestModule & { children: ModuleTreeNode[] }
type ModuleTreeRow = { node: ModuleTreeNode; level: number }

const moduleTree = computed<ModuleTreeNode[]>(() => {
  const nodes = new Map<number, ModuleTreeNode>()
  const roots: ModuleTreeNode[] = []
  for (const module of modules.value) nodes.set(module.id, { ...module, children: [] })
  for (const node of nodes.values()) {
    const parent = node.parent_id != null ? nodes.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  return roots
})

function flattenModuleTree(nodes: ModuleTreeNode[], level = 0): ModuleTreeRow[] {
  return nodes.flatMap((node) => [
    { node, level },
    ...(collapsedModules.value.has(node.id) ? [] : flattenModuleTree(node.children, level + 1)),
  ])
}

const visibleModuleRows = computed(() => flattenModuleTree(moduleTree.value))

async function loadModules() {
  modules.value = await listModules(projectId)
  const activeIds = new Set(modules.value.map((module) => module.id))
  collapsedModules.value = new Set(
    [...collapsedModules.value].filter((moduleId) => activeIds.has(moduleId)),
  )
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
  selectedRows.value = []
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

// 模块树操作
const moduleDialogVisible = ref(false)
const newModuleName = ref('')
const moduleCreating = ref(false)
const editingModuleId = ref<number | null>(null)
const creatingParentId = ref<number | null>(null)
const creatingParentName = computed(
  () => modules.value.find((module) => module.id === creatingParentId.value)?.name ?? '',
)
const moduleContextMenu = ref<{
  visible: boolean
  left: number
  top: number
  module: TestModule | null
}>({ visible: false, left: 0, top: 0, module: null })

function isModuleCollapsed(module: TestModule) {
  return collapsedModules.value.has(module.id)
}

function toggleModule(module: TestModule) {
  const next = new Set(collapsedModules.value)
  if (next.has(module.id)) next.delete(module.id)
  else next.add(module.id)
  collapsedModules.value = next
}

function closeModuleContextMenu() {
  moduleContextMenu.value.visible = false
}

function openModuleContextMenu(event: MouseEvent, module: TestModule) {
  if (!canWriteAssets.value) return
  event.preventDefault()
  event.stopPropagation()
  moduleContextMenu.value = {
    visible: true,
    left: Math.min(event.clientX, Math.max(8, window.innerWidth - 190)),
    top: Math.min(event.clientY, Math.max(8, window.innerHeight - 90)),
    module,
  }
}

function openCreateModule(parentId: number | null = null) {
  closeModuleContextMenu()
  editingModuleId.value = null
  creatingParentId.value = parentId
  newModuleName.value = ''
  moduleDialogVisible.value = true
}

function openEditModule(module: TestModule) {
  closeModuleContextMenu()
  editingModuleId.value = module.id
  creatingParentId.value = null
  newModuleName.value = module.name
  moduleDialogVisible.value = true
}

function openCreateChildModule(module: TestModule) {
  openCreateModule(module.id)
}

async function submitModule() {
  const name = newModuleName.value.trim()
  if (!name) {
    ElMessage.warning('请输入模块名称')
    return
  }
  moduleCreating.value = true
  try {
    const editingId = editingModuleId.value
    if (editingId != null) {
      await updateModule(editingId, { name })
      ElMessage.success('模块已更新')
    } else {
      const mod = await createModule(projectId, { name, parent_id: creatingParentId.value })
      if (creatingParentId.value != null) {
        const next = new Set(collapsedModules.value)
        next.delete(creatingParentId.value)
        collapsedModules.value = next
      }
      selectedModule.value = String(mod.id)
      filteredModuleId.value = mod.id
      page.value = 1
      ElMessage.success('模块已创建')
    }
    moduleDialogVisible.value = false
    editingModuleId.value = null
    creatingParentId.value = null
    await loadModules()
    await load()
  } finally {
    moduleCreating.value = false
  }
}

async function removeModule(module: TestModule) {
  closeModuleContextMenu()
  await ElMessageBox.confirm(
    `确认删除模块「${module.name}」？其子模块会提升到当前层级，模块内用例会变为未分组。`,
    '提示',
    { type: 'warning' },
  )
  await deleteModule(module.id)
  if (selectedModule.value === String(module.id)) {
    selectedModule.value = 'all'
    filteredModuleId.value = null
    page.value = 1
  }
  await Promise.all([loadModules(), load()])
  ElMessage.success('模块已删除')
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
  void loadModules()
  void load()
  window.addEventListener('click', closeModuleContextMenu)
})

onUnmounted(() => {
  window.removeEventListener('click', closeModuleContextMenu)
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
      <div
        v-for="row in visibleModuleRows"
        :key="row.node.id"
        class="tree-item tree-module"
        :class="{ active: selectedModule === String(row.node.id) }"
        :style="{ paddingLeft: `${12 + row.level * 18}px` }"
        @click="selectModule(String(row.node.id))"
        @contextmenu="openModuleContextMenu($event, row.node)"
      >
        <span class="tree-label">
          <span
            class="tree-chevron"
            :class="{ expanded: !isModuleCollapsed(row.node) }"
            :style="{ visibility: row.node.children.length ? 'visible' : 'hidden' }"
            title="展开/折叠"
            @click.stop="toggleModule(row.node)"
          >›</span>
          <el-icon class="module-folder-icon"><Folder /></el-icon>
          <span class="module-name">{{ row.node.name }}</span>
        </span>
        <span class="module-actions" v-if="canWriteAssets">
          <el-icon title="编辑模块" @click.stop="openEditModule(row.node)"><Edit /></el-icon>
          <el-icon class="module-delete" title="删除模块" @click.stop="removeModule(row.node)"><Delete /></el-icon>
        </span>
      </div>
      <el-button v-if="canWriteAssets" class="add-module" text type="primary" @click="openCreateModule()">
        <el-icon><Plus /></el-icon>
        <span>新增模块</span>
      </el-button>
      <div
        v-if="moduleContextMenu.visible && moduleContextMenu.module"
        class="module-context-menu"
        :style="{ left: `${moduleContextMenu.left}px`, top: `${moduleContextMenu.top}px` }"
        @click.stop
      >
        <button type="button" @click="openCreateChildModule(moduleContextMenu.module!)">新建子模块</button>
        <button type="button" @click="openEditModule(moduleContextMenu.module!)">编辑模块</button>
        <button type="button" @click="removeModule(moduleContextMenu.module!)">删除模块</button>
      </div>
    </div>

    <el-dialog
      v-model="moduleDialogVisible"
      :title="editingModuleId != null ? '编辑模块' : creatingParentId != null ? '新建子模块' : '新增模块'"
      width="420px"
    >
      <el-form label-width="80px" @submit.prevent="submitModule">
        <div v-if="creatingParentName" class="module-parent-hint">父模块：{{ creatingParentName }}</div>
        <el-form-item label="模块名称" required>
          <el-input
            v-model="newModuleName"
            placeholder="请输入模块名称"
            maxlength="255"
            @keyup.enter="submitModule"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="moduleDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="moduleCreating" :disabled="!newModuleName.trim()" @click="submitModule">
          {{ editingModuleId != null ? '保存' : '创建' }}
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
        <el-button v-if="canWriteAssets" type="danger" plain :disabled="!selectedRows.length || deleting" :loading="deleting" @click="removeSelected">批量删除</el-button>
        <el-button v-if="canWriteAssets" type="primary" @click="openCreate">新建用例</el-button>
      </div>

      <div v-if="selectedRows.length" class="batch-bar">
        <span>已选 {{ selectedRows.length }} 个用例</span>
      </div>


      <el-table v-loading="loading" :data="items" stripe row-key="id" @selection-change="handleSelectionChange">
        <el-table-column v-if="canWriteAssets" type="selection" width="42" />
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="case-name" :class="{ clickable: canWriteAssets }" @click="canWriteAssets && openEdit(row as TestCase)">{{ row.name }}</span>
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
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <RunButton v-if="(row as TestCase).status !== 'disabled'" :type="'case'" :id="(row as TestCase).id" :name="(row as TestCase).name" />
            <template v-if="canWriteAssets">
              <el-button size="small" type="primary" text @click="openEdit(row as TestCase)">编辑</el-button>
              <el-button size="small" text @click="clone(row as TestCase)">克隆</el-button>
              <el-button size="small" type="danger" text @click="remove(row as TestCase)">删除</el-button>
            </template>
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
  width: 260px;
  flex-shrink: 0;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px;
  align-self: flex-start;
  max-height: calc(100vh - 150px);
  overflow-y: auto;
  box-sizing: border-box;
}
.tree-head {
  padding: 8px 12px;
}
.tree-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
.tree-label {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 4px;
}
.tree-chevron {
  width: 14px;
  flex: 0 0 14px;
  color: var(--text-2);
  font-size: 20px;
  line-height: 12px;
  text-align: center;
  cursor: pointer;
  transform: rotate(0deg);
  transition: transform 0.15s ease;
}
.tree-chevron.expanded {
  transform: rotate(90deg);
}
.module-folder-icon {
  color: #e6b800;
  flex: 0 0 auto;
}
.module-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.module-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-2);
  font-size: 13px;
  opacity: 0;
}
.tree-module:hover .module-actions,
.tree-module.active .module-actions {
  opacity: 1;
}
.module-actions .el-icon {
  cursor: pointer;
}
.module-actions .el-icon:hover {
  color: var(--primary);
}
.module-actions .module-delete:hover {
  color: var(--el-color-danger);
}
.module-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 150px;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card-bg);
  box-shadow: var(--shadow-md);
}
.module-context-menu button {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-1);
  text-align: left;
  cursor: pointer;
  font-size: 13px;
}
.module-context-menu button:hover {
  background: var(--primary-light);
  color: var(--primary);
}
.module-parent-hint {
  margin: 0 0 10px 80px;
  color: var(--text-2);
  font-size: 12px;
}
.add-module {
  width: 100%;
  margin-top: 8px;
  justify-content: center;
  border: 1px dashed var(--border);
  border-radius: 6px;
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
