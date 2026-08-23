<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { listProjects } from '@/api/projects'
import {
  LOCATOR_TYPES,
  copyElement,
  createElement,
  createElementGroup,
  deleteElement,
  deleteElementGroup,
  elementPageFilter,
  elementPages,
  elementUsage,
  listElements,
  totalElementCount,
  updateElement,
  type ElementPageCount,
  type TestElement,
} from '@/api/elements'

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
  locator_type: string
  locator_value: string
  description: string
}>({
  project_id: undefined,
  name: '',
  page_name: '',
  platform: 'both',
  locator_type: 'id',
  locator_value: '',
  description: '',
})

const groupDialogVisible = ref(false)
const groupName = ref('')

const usageDialogVisible = ref(false)
const usageCases = ref<{ case_id: number; case_name: string }[]>([])

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
    locator_type: 'id',
    locator_value: '',
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
    locator_type: row.locator_type,
    locator_value: row.locator_value,
    description: row.description ?? '',
  }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.project_id) {
    ElMessage.warning('请选择项目')
    return
  }
  if (!form.value.name || !form.value.locator_value) {
    ElMessage.warning('名称和定位值必填')
    return
  }
  const isCreate = !editingId.value
  const payload = { ...form.value, project_id: form.value.project_id, page_name: form.value.page_name || null }
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
        <el-table-column label="定位方式" width="150">
          <template #default="{ row }">{{ locatorLabel(row.locator_type) }}</template>
        </el-table-column>
        <el-table-column prop="locator_value" label="定位值" min-width="170" show-overflow-tooltip />
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑元素' : '新建元素'" width="560px">
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
        <el-form-item label="定位方式">
          <el-select v-model="form.locator_type" class="full">
            <el-option v-for="t in LOCATOR_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="定位值" required>
          <el-input v-model="form.locator_value" :placeholder="locatorValuePlaceholder(form.locator_type)" />
          <div v-if="form.locator_type === 'resource_id'" class="field-help">
            输入和清空动作会自动定位其下的 android.widget.EditText。
          </div>
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
</style>
