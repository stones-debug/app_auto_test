<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Delete, Edit, Folder, Plus } from '@element-plus/icons-vue'

import { useAuthStore } from '@/stores/auth'
import { listProjects } from '@/api/projects'
import SmartLocatorEditor from '@/components/SmartLocatorEditor.vue'
import {
  LOCATOR_TYPES,
  copyElement,
  createElement,
  createElementGroup,
  deleteElement,
  deleteElementGroup,
  downloadElementImportTemplate,
  elementPageFilter,
  elementPages,
  elementUsage,
  exportElements,
  importElements,
  isReservedElementPageGroupName,
  listElements,
  totalElementCount,
  updateElement,
  updateElementGroup,
  type ElementImportError,
  type ElementPageCount,
  type TestElement,
} from '@/api/elements'
import { buildLocatorPayload, cloneSmartConfig, smartLocatorSummary, validateSmartConfig, type SmartLocatorConfig } from '@/utils/smartLocator'

const auth = useAuthStore()
const route = useRoute()

const isProjectMode = computed(() => route.params.projectId != null)

const loading = ref(false)
const items = ref<TestElement[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const platform = ref('')
const routeProjectId = Number(route.params.projectId)
const projectFilter = ref<number | undefined>(
  Number.isInteger(routeProjectId) && routeProjectId > 0 ? routeProjectId : undefined,
)
const pageGroups = ref<ElementPageCount[]>([])
const allTotal = computed(() => totalElementCount(pageGroups.value))
const selectedPage = ref('all')
const projects = ref<{ id: number; name: string }[]>([])
const collapsedGroups = ref<Set<number>>(new Set())

type PageTreeNode = ElementPageCount & { children: PageTreeNode[] }
type PageTreeRow = { node: PageTreeNode; level: number }

const pageTree = computed<PageTreeNode[]>(() => {
  const nodes = new Map<number, PageTreeNode>()
  const roots: PageTreeNode[] = []
  for (const group of pageGroups.value) {
    if (group.group_id == null) continue
    nodes.set(group.group_id, { ...group, children: [] })
  }
  for (const node of nodes.values()) {
    const parent = node.parent_id != null ? nodes.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  const implicitPages = pageGroups.value
    .filter((group) => group.group_id == null)
    .map((group) => ({ ...group, children: [] }))
  return [...roots, ...implicitPages]
})

function flattenPageTree(nodes: PageTreeNode[], level = 0): PageTreeRow[] {
  return nodes.flatMap((node) => [
    { node, level },
    ...(node.group_id != null && collapsedGroups.value.has(node.group_id)
      ? []
      : flattenPageTree(node.children, level + 1)),
  ])
}

const visiblePageGroups = computed(() => flattenPageTree(pageTree.value))

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref<{
  project_id?: number
  name: string
  page_name: string
  platform: string
  scope: string
  locator_type: string
  locator_value: string
  locator_config: SmartLocatorConfig | null
  description: string
}>({
  project_id: undefined,
  name: '',
  page_name: '',
  platform: 'both',
  scope: 'all',
  locator_type: 'id',
  locator_value: '',
  locator_config: null,
  description: '',
})

const groupDialogVisible = ref(false)
const editingGroupId = ref<number | null>(null)
const creatingParentId = ref<number | null>(null)
const groupName = ref('')
const creatingParentName = computed(
  () => pageGroups.value.find((group) => group.group_id === creatingParentId.value)?.page_name ?? '',
)
const groupContextMenu = ref<{
  visible: boolean
  left: number
  top: number
  group: ElementPageCount | null
}>({ visible: false, left: 0, top: 0, group: null })

const usageDialogVisible = ref(false)
const usageCases = ref<{ case_id: number; case_name: string }[]>([])
const exportLoading = ref(false)
const importDialogVisible = ref(false)
const importLoading = ref(false)
const importFile = ref<File | null>(null)
const importErrors = ref<ElementImportError[]>([])
const importInput = ref<HTMLInputElement>()

function canEdit(row: TestElement) {
  return !!auth.user && row.created_by === auth.user.id
}

function canManageGroup(group: ElementPageCount) {
  return !!auth.user && group.group_id != null && !isReservedElementPageGroupName(group.page_name)
}

function isCollapsed(group: ElementPageCount) {
  return group.group_id != null && collapsedGroups.value.has(group.group_id)
}

function toggleGroup(group: ElementPageCount) {
  if (group.group_id == null) return
  const next = new Set(collapsedGroups.value)
  if (next.has(group.group_id)) next.delete(group.group_id)
  else next.add(group.group_id)
  collapsedGroups.value = next
}

function closeGroupContextMenu() {
  groupContextMenu.value.visible = false
}

function openGroupContextMenu(event: MouseEvent, group: ElementPageCount) {
  if (!canManageGroup(group) || group.group_id == null) return
  event.preventDefault()
  event.stopPropagation()
  groupContextMenu.value = {
    visible: true,
    left: Math.min(event.clientX, Math.max(8, window.innerWidth - 190)),
    top: Math.min(event.clientY, Math.max(8, window.innerHeight - 90)),
    group,
  }
}

async function load() {
  loading.value = true
  try {
    const data = await listElements({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value || undefined,
      platform: platform.value || undefined,
      project_id: projectFilter.value,
      page_name: elementPageFilter(selectedPage.value),
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadPages() {
  pageGroups.value = await elementPages(projectFilter.value)
}

async function loadProjects() {
  const data = await listProjects({ page: 1, page_size: 100 })
  projects.value = data.items
  if (isProjectMode.value) {
    // 项目内元素库：固定当前项目上下文
    projectFilter.value = Number(route.params.projectId)
    page.value = 1
    await load()
    return
  }
  // 从全局元素页跳入时可带 project 筛选（如项目概览跳转）
  const q = Number(route.query.project)
  if (q) {
    projectFilter.value = q
    page.value = 1
    await Promise.all([loadPages(), load()])
  }
}

function selectPage(name: string) {
  selectedPage.value = name
  page.value = 1
  load()
}

function openCreate() {
  editingId.value = null
  form.value = {
    project_id: projectFilter.value ?? projects.value[0]?.id,
    name: '',
    page_name: '',
    platform: 'both',
    scope: 'all',
    locator_type: 'id',
    locator_value: '',
    locator_config: null,
    description: '',
  }
  dialogVisible.value = true
}

function openEdit(row: TestElement) {
  editingId.value = row.id
  form.value = {
    project_id: row.project_id,
    name: row.name,
    page_name: row.page_name ?? '',
    platform: row.platform ?? 'both',
    scope: row.scope ?? 'all',
    locator_type: row.locator_type,
    locator_value: row.locator_value ?? '',
    locator_config: row.locator_type === 'smart'
      ? cloneSmartConfig(row.locator_config)
      : null,
    description: row.description ?? '',
  }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.project_id) {
    ElMessage.warning('请选择项目')
    return
  }
  if (!form.value.name) {
    ElMessage.warning('请填写名称')
    return
  }
  if (form.value.locator_type === 'smart') {
    const config = form.value.locator_config
    if (!config) {
      ElMessage.warning('请完成智能定位配置')
      return
    }
    const errors = validateSmartConfig(config)
    if (errors.length) {
      ElMessage.error('智能定位配置有误：' + errors[0])
      return
    }
  } else if (!form.value.locator_value) {
    ElMessage.warning('请填写定位值')
    return
  }
  const isCreate = !editingId.value
  const payload = {
    ...form.value,
    project_id: form.value.project_id,
    page_name: form.value.page_name || null,
    scope: form.value.scope.trim() || 'all',
    ...buildLocatorPayload(form.value.locator_type, form.value.locator_value, form.value.locator_config),
  }
  if (editingId.value) {
    await updateElement(editingId.value, payload)
    ElMessage.success('已更新')
  } else {
    await createElement(payload)
    ElMessage.success('已创建')
  }
  dialogVisible.value = false
  if (isCreate) {
    // 新建元素属于当前分组时保留筛选上下文，否则回到「全部」保证新元素可见。
    const createdPage = form.value.page_name.trim() || '未分组'
    if (selectedPage.value !== createdPage) selectedPage.value = 'all'
    page.value = 1
    await Promise.all([loadPages(), load()])
  } else {
    // 编辑可能改变或清空页面分组，列表与左侧计数必须一起刷新。
    await Promise.all([loadPages(), load()])
  }
}

async function remove(row: TestElement) {
  await ElMessageBox.confirm(`确认删除元素「${row.name}」？`, '提示', { type: 'warning' })
  await deleteElement(row.id)
  ElMessage.success('已删除')
  await Promise.all([loadPages(), load()])
}

async function copyRow(row: TestElement) {
  await copyElement(row.id)
  ElMessage.success(`已复制「${row.name}」`)
  await Promise.all([loadPages(), load()])
}

async function showUsage(row: TestElement) {
  usageCases.value = await elementUsage(row.id)
  usageDialogVisible.value = true
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

async function exportExcel() {
  exportLoading.value = true
  try {
    const blob = await exportElements({
      keyword: keyword.value || undefined,
      platform: platform.value || undefined,
      page_name: elementPageFilter(selectedPage.value),
      project_id: projectFilter.value,
    })
    saveBlob(blob, 'elements-export.xlsx')
    ElMessage.success('元素已导出')
  } finally {
    exportLoading.value = false
  }
}

function openImport() {
  if (!isProjectMode.value || !projectFilter.value) {
    ElMessage.warning('请从具体项目的元素库进入批量导入')
    return
  }
  if (form.value.page_name && isReservedElementPageGroupName(form.value.page_name)) {
    ElMessage.warning('页面分组名称不能使用“all”“全部”或“未分组”')
    return
  }
  importFile.value = null
  importErrors.value = []
  if (importInput.value) importInput.value.value = ''
  importDialogVisible.value = true
}

function chooseImportFile() {
  // 允许用户修正后再次选择同一路径文件；否则部分浏览器不会触发 change。
  if (importInput.value) importInput.value.value = ''
  importInput.value?.click()
}

function onImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    ElMessage.warning('只支持 .xlsx 文件')
    input.value = ''
    return
  }
  if (file.size > 5 * 1024 * 1024) {
    ElMessage.warning('文件大小不能超过 5 MB')
    input.value = ''
    return
  }
  importFile.value = file
  importErrors.value = []
}

async function downloadTemplate() {
  if (!projectFilter.value) return
  const blob = await downloadElementImportTemplate(projectFilter.value)
  saveBlob(blob, 'element-import-template.xlsx')
}

function importErrorDetail(error: unknown): { message: string; errors: ElementImportError[] } {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object') {
    const typed = detail as { message?: unknown; errors?: unknown }
    const errors = Array.isArray(typed.errors) ? typed.errors.filter((item): item is ElementImportError => {
      if (!item || typeof item !== 'object') return false
      const value = item as Record<string, unknown>
      return typeof value.row === 'number' && typeof value.field === 'string' && typeof value.message === 'string'
    }) : []
    return { message: typeof typed.message === 'string' ? typed.message : '导入失败', errors }
  }
  return { message: '导入失败，请检查文件后重试', errors: [] }
}

async function submitImport() {
  if (!projectFilter.value || !importFile.value) {
    ElMessage.warning('请选择要导入的 Excel 文件')
    return
  }
  importLoading.value = true
  importErrors.value = []
  try {
    const result = await importElements(projectFilter.value, importFile.value)
    ElMessage.success(`导入完成：新建 ${result.created}，更新 ${result.updated}`)
    importDialogVisible.value = false
    await Promise.all([loadPages(), load()])
  } catch (error) {
    const detail = importErrorDetail(error)
    importErrors.value = detail.errors
    ElMessage.error(detail.message)
  } finally {
    importLoading.value = false
  }
}

function openCreateGroup() {
  editingGroupId.value = null
  creatingParentId.value = null
  groupName.value = ''
  groupDialogVisible.value = true
}

function openCreateChildGroup(parent: ElementPageCount) {
  closeGroupContextMenu()
  editingGroupId.value = null
  creatingParentId.value = parent.group_id ?? null
  groupName.value = ''
  groupDialogVisible.value = true
}

function openEditGroup(group: ElementPageCount) {
  if (!group.group_id) return
  closeGroupContextMenu()
  editingGroupId.value = group.group_id
  creatingParentId.value = null
  groupName.value = group.page_name
  groupDialogVisible.value = true
}

async function saveGroup() {
  const name = groupName.value.trim()
  if (!name) {
    ElMessage.warning('请输入分组名称')
    return
  }
  if (isReservedElementPageGroupName(name)) {
    ElMessage.warning('页面分组名称不能使用“all”“全部”或“未分组”')
    return
  }
  const oldName = pageGroups.value.find((group) => group.group_id === editingGroupId.value)?.page_name
  if (editingGroupId.value) {
    await updateElementGroup(editingGroupId.value, name)
    if (selectedPage.value === oldName) selectedPage.value = name
    ElMessage.success('分组已更新')
  } else {
    await createElementGroup(name, creatingParentId.value)
    if (creatingParentId.value != null) {
      const next = new Set(collapsedGroups.value)
      next.delete(creatingParentId.value)
      collapsedGroups.value = next
    }
    selectedPage.value = name
    ElMessage.success('分组已创建')
  }
  groupDialogVisible.value = false
  editingGroupId.value = null
  creatingParentId.value = null
  groupName.value = ''
  page.value = 1
  await Promise.all([loadPages(), load()])
}

async function removeGroup(g: ElementPageCount) {
  if (!g.group_id) return
  closeGroupContextMenu()
  const detail = g.count > 0 ? `其中 ${g.count} 个元素将自动变为“未分组”` : '该分组当前没有元素'
  await ElMessageBox.confirm(`确认删除自定义分组「${g.page_name}」？${detail}`, '提示', { type: 'warning' })
  await deleteElementGroup(g.group_id)
  if (selectedPage.value === g.page_name) {
    selectedPage.value = 'all'
  }
  const next = new Set(collapsedGroups.value)
  next.delete(g.group_id)
  collapsedGroups.value = next
  page.value = 1
  await Promise.all([loadPages(), load()])
}

function locatorLabel(type: string) {
  return LOCATOR_TYPES.find((t) => t.value === type)?.label ?? type
}

function locatorValuePlaceholder(type: string) {
  if (type === 'resource_id') {
    return '填写纯 resource-id，如 src-views-login-input-username'
  }
  return "如 com.demo:id/btn_login / //*[@text='登录']"
}

function smartSummary(row: TestElement): string {
  if (row.locator_type === 'smart') return smartLocatorSummary(row.locator_config)
  return row.locator_value ?? ''
}

type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger'

function platformType(p: string): TagType {
  return ({ both: 'success', android: 'primary', ios: 'warning' } as Record<string, TagType>)[p] ?? 'info'
}

function platformLabel(p: string) {
  return ({ both: '通用', android: 'Android', ios: 'iOS' } as Record<string, string>)[p] ?? p
}

onMounted(() => {
  loadPages()
  loadProjects()
  load()
  window.addEventListener('click', closeGroupContextMenu)
})

onUnmounted(() => {
  window.removeEventListener('click', closeGroupContextMenu)
})
</script>

<template>
  <div class="elements-layout">
    <!-- 左：页面分组 -->
    <div class="page-tree">
      <div class="tree-head v2-card-title">页面分组</div>
      <div class="tree-item" :class="{ active: selectedPage === 'all' }" @click="selectPage('all')">
        <span>全部</span><span class="count">{{ allTotal }}</span>
      </div>
      <div
        v-for="row in visiblePageGroups"
        :key="row.node.page_name"
        class="tree-item tree-group"
        :class="{ active: selectedPage === row.node.page_name }"
        :style="{ paddingLeft: `${12 + row.level * 18}px` }"
        @click="selectPage(row.node.page_name)"
        @contextmenu="openGroupContextMenu($event, row.node)"
      >
        <span class="tree-label">
          <span
            class="tree-chevron"
            :class="{ expanded: !isCollapsed(row.node) }"
            :style="{ visibility: row.node.children.length ? 'visible' : 'hidden' }"
            title="展开/折叠"
            @click.stop="toggleGroup(row.node)"
          >›</span>
          <el-icon class="group-folder-icon"><Folder /></el-icon>
          <span class="group-name">{{ row.node.page_name }}</span>
        </span>
        <span class="group-right">
          <span v-if="canManageGroup(row.node)" class="group-actions">
            <el-icon class="group-action-icon" title="编辑页面" @click.stop="openEditGroup(row.node)"><Edit /></el-icon>
            <el-icon class="group-action-icon group-del-icon" title="删除页面" @click.stop="removeGroup(row.node)"><Delete /></el-icon>
          </span>
          <span class="count">{{ row.node.count }}</span>
        </span>
      </div>
      <el-button class="add-group-btn" text type="primary" @click="openCreateGroup">
        <el-icon><Plus /></el-icon>
        <span>新增分组</span>
      </el-button>
      <div
        v-if="groupContextMenu.visible && groupContextMenu.group"
        class="group-context-menu"
        :style="{ left: `${groupContextMenu.left}px`, top: `${groupContextMenu.top}px` }"
        @click.stop
      >
        <button type="button" @click="openCreateChildGroup(groupContextMenu.group)">新建页面</button>
      </div>
    </div>

    <!-- 右：元素列表 -->
    <div class="elements-main">
      <div class="toolbar-card">
        <el-input v-model="keyword" placeholder="按名称搜索" clearable class="search" @keyup.enter="page = 1; load()" />
        <el-select v-model="projectFilter" placeholder="全部项目" clearable :disabled="isProjectMode" class="platform" @change="page = 1; load()">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-select v-model="platform" placeholder="平台" clearable class="platform" @change="page = 1; load()">
          <el-option label="Android" value="android" />
          <el-option label="iOS" value="ios" />
          <el-option label="通用" value="both" />
        </el-select>
        <el-button type="primary" @click="page = 1; load()">搜索</el-button>
        <span class="spacer"></span>
        <el-button v-if="isProjectMode" @click="openImport">导入 Excel</el-button>
        <el-button :loading="exportLoading" @click="exportExcel">导出 Excel</el-button>
        <el-button type="primary" @click="openCreate">新建元素</el-button>
      </div>

      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="project_name" label="项目" min-width="120">
          <template #default="{ row }">
            <span>{{ row.project_name ?? '未知项目' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="page_name" label="页面" min-width="110" />
        <el-table-column label="平台" width="90">
          <template #default="{ row }">
            <el-tag :type="platformType(row.platform)" size="small">{{ platformLabel(row.platform) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="scope" label="适用范围" width="110" show-overflow-tooltip />
        <el-table-column label="定位方式" width="150">
          <template #default="{ row }">{{ locatorLabel(row.locator_type) }}</template>
        </el-table-column>
        <el-table-column prop="locator_value" label="定位值" min-width="170">
          <template #default="{ row }">
            <el-tag v-if="row.locator_type === 'smart'" size="small" type="primary" class="smart-tag" :title="smartSummary(row as TestElement)">
              {{ smartSummary(row as TestElement) }}
            </el-tag>
            <span v-else class="locator-cell">{{ smartSummary(row as TestElement) || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建者" width="110">
          <template #default="{ row }">{{ row.created_by_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text @click="showUsage(row as TestElement)">引用</el-button>
            <el-button size="small" text @click="copyRow(row as TestElement)">复制</el-button>
            <template v-if="canEdit(row as TestElement)">
              <el-button size="small" text @click="openEdit(row as TestElement)">编辑</el-button>
              <el-button size="small" type="danger" text @click="remove(row as TestElement)">删除</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑元素' : '新建元素'" width="800px">
      <el-form label-width="90px">
        <el-form-item label="项目" required>
          <el-select v-model="form.project_id" class="full" :disabled="isProjectMode">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="页面">
          <el-select
            v-model="form.page_name"
            filterable
            allow-create
            default-first-option
            class="full"
            placeholder="选择页面分组，如 登录页"
          >
            <el-option label="未分组" value="" />
            <el-option v-for="g in pageGroups" :key="g.page_name" :label="g.page_name" :value="g.page_name" />
          </el-select>
        </el-form-item>
        <el-form-item label="平台">
          <el-radio-group v-model="form.platform">
            <el-radio value="android">Android</el-radio>
            <el-radio value="ios">iOS</el-radio>
            <el-radio value="both">通用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="适用范围">
          <el-input v-model="form.scope" placeholder="不填默认为 all（所有）" />
        </el-form-item>
        <el-form-item label="定位方式">
          <el-select v-model="form.locator_type" class="full">
            <el-option v-for="t in LOCATOR_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item :label="form.locator_type === 'smart' ? '定位配置' : '定位值'" required>
          <SmartLocatorEditor v-if="form.locator_type === 'smart'" v-model="form.locator_config" class="full" />
          <template v-else>
            <el-input v-model="form.locator_value" :placeholder="locatorValuePlaceholder(form.locator_type)" />
            <div v-if="form.locator_type === 'resource_id'" class="field-help">
              输入和清空动作会自动定位其下的 android.widget.EditText。
            </div>
          </template>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="groupDialogVisible" :title="editingGroupId ? '编辑页面' : creatingParentId ? '新建子页面' : '新增页面分组'" width="420px">
      <div v-if="creatingParentId" class="group-parent-hint">归属层级：{{ creatingParentName }}</div>
      <el-input v-model="groupName" placeholder="输入分组名称，如 登录页" @keyup.enter="saveGroup" />
      <template #footer>
        <el-button @click="groupDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveGroup">{{ editingGroupId ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <input ref="importInput" type="file" accept=".xlsx" hidden @change="onImportFileChange" />
    <el-dialog v-model="importDialogVisible" title="批量导入元素" width="640px">
      <el-alert type="info" :closable="false" show-icon title="元素ID为空时新建；填写元素ID时仅更新当前项目内由你创建的元素。任意一行错误都会整批回滚。" />
      <div class="import-actions">
        <el-button @click="downloadTemplate">下载 Excel 模板</el-button>
        <el-button type="primary" plain @click="chooseImportFile">选择 .xlsx 文件</el-button>
        <span v-if="importFile" class="import-file">{{ importFile.name }}（{{ Math.ceil(importFile.size / 1024) }} KB）</span>
      </div>
      <el-empty v-if="!importFile && importErrors.length === 0" :image-size="60" description="请选择 Excel 文件" />
      <el-table v-if="importErrors.length" :data="importErrors" max-height="260" class="import-errors">
        <el-table-column prop="row" label="行号" width="70" />
        <el-table-column prop="field" label="字段" width="220" />
        <el-table-column prop="message" label="错误原因" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="importLoading" :disabled="!importFile" @click="submitImport">开始导入</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="usageDialogVisible" title="被以下用例引用" width="480px">
      <el-empty v-if="usageCases.length === 0" description="暂无用例引用" />
      <el-table v-else :data="usageCases">
        <el-table-column prop="case_id" label="用例ID" width="90" />
        <el-table-column prop="case_name" label="用例名" />
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.elements-layout {
  display: flex;
  gap: 16px;
}
.page-tree {
  width: 260px;
  flex-shrink: 0;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px;
  align-self: flex-start;
}
.tree-head {
  padding: 8px 12px 12px;
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
.group-folder-icon {
  color: #e6b800;
  flex: 0 0 auto;
}
.group-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-item .count {
  font-size: 12px;
  color: var(--text-2);
  background: var(--bg);
  border-radius: 10px;
  padding: 0 8px;
}
.group-right {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.group-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.group-action-icon {
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
}
.group-action-icon:hover {
  color: var(--primary);
}
.group-del-icon {
  color: var(--text-2);
}
.group-del-icon:hover {
  color: var(--el-color-danger);
}
.group-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 150px;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card-bg);
  box-shadow: var(--shadow-md);
}
.group-context-menu button {
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
.group-context-menu button:hover {
  background: var(--primary-light);
  color: var(--primary);
}
.group-parent-hint {
  margin-bottom: 10px;
  color: var(--text-2);
  font-size: 12px;
}
.el-button {
  margin-left: 0px;
}
.add-group-btn {
  width: 100%;
  margin-top: 8px;
  justify-content: flex-start;
}
.elements-main {
  flex: 1;
  min-width: 0;
}
.search {
  width: 200px;
}
.platform {
  width: 140px;
}
.full {
  width: 100%;
}
.field-help {
  color: var(--text-2);
  font-size: 12px;
  line-height: 20px;
}
.import-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 18px 0 12px;
}
.import-file {
  color: var(--text-2);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.import-errors {
  margin-top: 12px;
}
.smart-tag {
  display: inline-block;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
.locator-cell {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
</style>
