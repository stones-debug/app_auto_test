<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

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
  listElements,
  totalElementCount,
  updateElement,
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
const projectFilter = ref<number | undefined>()
const pageGroups = ref<ElementPageCount[]>([])
const allTotal = computed(() => totalElementCount(pageGroups.value))
const selectedPage = ref('all')
const projects = ref<{ id: number; name: string }[]>([])

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
const groupName = ref('')

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
  pageGroups.value = await elementPages()
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
    await load()
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
    // 新建后回到「全部」并刷新分组树/第 1 页，保证新元素立即可见
    selectedPage.value = 'all'
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
  importFile.value = null
  importErrors.value = []
  if (importInput.value) importInput.value.value = ''
  importDialogVisible.value = true
}

function chooseImportFile() {
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

async function addGroup() {
  const name = groupName.value.trim()
  if (!name) {
    ElMessage.warning('请输入分组名称')
    return
  }
  await createElementGroup(name)
  groupDialogVisible.value = false
  groupName.value = ''
  selectedPage.value = name
  page.value = 1
  await Promise.all([loadPages(), load()])
}

async function removeGroup(g: ElementPageCount) {
  if (!g.group_id || g.count > 0) return
  await ElMessageBox.confirm(`确认删除自定义分组「${g.page_name}」？`, '提示', { type: 'warning' })
  await deleteElementGroup(g.group_id)
  if (selectedPage.value === g.page_name) {
    selectedPage.value = 'all'
    page.value = 1
    await Promise.all([loadPages(), load()])
    return
  }
  await loadPages()
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
      <div v-for="g in pageGroups" :key="g.page_name" class="tree-item tree-group" :class="{ active: selectedPage === g.page_name }" @click="selectPage(g.page_name)">
        <span>{{ g.page_name }}</span>
        <span class="group-right">
          <el-icon v-if="g.group_id && g.count === 0" class="group-del-icon" title="删除分组" @click.stop="removeGroup(g)"><Delete /></el-icon>
          <span class="count">{{ g.count }}</span>
        </span>
      </div>
      <el-button class="add-group-btn" text type="primary" @click="groupDialogVisible = true">
        <el-icon><Plus /></el-icon>
        <span>新增分组</span>
      </el-button>
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

    <el-dialog v-model="groupDialogVisible" title="新增页面分组" width="420px">
      <el-input v-model="groupName" placeholder="输入分组名称，如 登录页" @keyup.enter="addGroup" />
      <template #footer>
        <el-button @click="groupDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="addGroup">创建</el-button>
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
  width: 220px;
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
.group-del-icon {
  font-size: 13px;
  color: var(--text-2);
}
.group-del-icon:hover {
  color: var(--el-color-danger);
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
