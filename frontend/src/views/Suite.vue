<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { MoreFilled, Plus, Search } from '@element-plus/icons-vue'
import Draggable from 'vuedraggable'

import {
  addSuiteCases,
  createSuite,
  deleteSuite,
  deleteVariable,
  getSuite,
  listSuiteCases,
  listSuites,
  listVariables,
  removeSuiteCase,
  reorderSuiteCases,
  updateSuite,
  updateVariable,
  type Suite,
  type SuiteCase,
  type Variable,
} from '@/api/suites'
import { listCases, normalizeStep, validateStep, type Step } from '@/api/cases'
import CaseStepEditor from '@/components/CaseStepEditor.vue'
import RunButton from '@/components/RunButton.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useUnsavedChanges } from '@/composables/useUnsavedChanges'
import { formatDateTime } from '@/utils/format'
import { getGroupSelectionState, setGroupSelection } from '@/utils/suiteCaseSelection'

const route = useRoute()
const projectId = Number(route.params.projectId)

/* ============ 左侧：套件列表 ============ */
const suites = ref<Suite[]>([])
const loadingSuites = ref(false)
const activeSuite = ref<number | null>(null)
const keyword = ref('')
const sortBy = ref<'updated' | 'name' | 'cases'>('updated')

type StatusTag = { label: string; type: 'success' | 'info' | 'warning' }

// 套件启用状态映射（后端 TestSuite.status 默认 active，标识 active/disabled 资产语义）
function suiteStatusMeta(status: string): StatusTag {
  if (status === 'active') return { label: '启用', type: 'success' }
  if (status === 'disabled') return { label: '停用', type: 'info' }
  return { label: status, type: 'warning' }
}

// 仅前端做搜索与排序，不改变后端接口约定；local compare / copy 避免污染原始数组
const filteredSuites = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const base = kw
    ? suites.value.filter(
        (s) => s.name.toLowerCase().includes(kw) || (s.description ?? '').toLowerCase().includes(kw),
      )
    : [...suites.value]
  switch (sortBy.value) {
    case 'name':
      return base.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
    case 'cases':
      return base.sort((a, b) => b.case_count - a.case_count)
    default:
      return base.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
  }
})

/* ============ 右侧：套件详情 ============ */
const loadingDetail = ref(false)
const suiteDetail = ref<Suite | null>(null)
const suiteCases = ref<SuiteCase[]>([])
const setupSteps = ref<Step[]>([])
const teardownSteps = ref<Step[]>([])
const savingSteps = ref(false)
const { dirty, markDirty, markSaved } = useUnsavedChanges()
let stepsLoaded = false

// 仅步骤改动启用未保存守卫
function onSetupStepsChange(steps: Step[]) {
  setupSteps.value = steps
  if (stepsLoaded) markDirty()
}

function onTeardownStepsChange(steps: Step[]) {
  teardownSteps.value = steps
  if (stepsLoaded) markDirty()
}

/* ---------- 套件信息（新建 / 编辑） ---------- */
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ name: '', description: '' })
const savingSuite = ref(false)

/* ---------- 套件变量 ---------- */
const suiteVars = ref<Variable[]>([])
const varEditing = ref<{ id: number; value: string } | null>(null)
const savingVar = ref(false)

/* ---------- 添加用例 ---------- */
interface AddCaseCandidate {
  id: number
  name: string
  module_name: string | null
  status: string
}

const addDialogVisible = ref(false)
const addKeyword = ref('')
const allCases = ref<AddCaseCandidate[]>([])
const selectedIds = ref<Set<number>>(new Set())
const addingCases = ref(false)
const loadingAddCases = ref(false)

const filteredCases = computed(() => {
  const kw = addKeyword.value.trim().toLowerCase()
  if (!kw) return allCases.value
  return allCases.value.filter((c) => c.name.toLowerCase().includes(kw))
})

// 按模块分组，便于批量挑选；未分组归入「未分组」
interface CaseGroup {
  name: string
  cases: AddCaseCandidate[]
}

const groupedCases = computed(() => {
  const groups: CaseGroup[] = []
  const map = new Map<string, AddCaseCandidate[]>()
  for (const c of filteredCases.value) {
    const key = c.module_name || '未分组'
    const arr = map.get(key)
    if (arr) arr.push(c)
    else map.set(key, [c])
  }
  for (const [name, cases] of map) groups.push({ name, cases })
  return groups
})

function caseStatusMeta(status: string): StatusTag {
  if (status === 'active') return { label: '启用', type: 'success' }
  if (status === 'disabled') return { label: '停用', type: 'info' }
  if (status === 'draft') return { label: '草稿', type: 'warning' }
  return { label: status, type: 'info' }
}

/* ============ 加载 ============ */
async function loadSuites() {
  loadingSuites.value = true
  try {
    suites.value = await listSuites(projectId)
    // 首次进入且尚未选中时默认选中第一个套件
    if (!activeSuite.value && suites.value.length > 0) {
      await selectSuite(suites.value[0].id)
    }
  } finally {
    loadingSuites.value = false
  }
}

// opts.preserveSteps=true 用于「非步骤」操作后的详情刷新：保留本地未保存的步骤改动，仅回填其它字段
async function selectSuite(id: number, opts?: { preserveSteps?: boolean }) {
  activeSuite.value = id
  loadingDetail.value = true
  try {
    const detail = await getSuite(id)
    if (opts?.preserveSteps) {
      suiteDetail.value = { ...detail, setup_steps: setupSteps.value, teardown_steps: teardownSteps.value }
    } else {
      suiteDetail.value = detail
      setupSteps.value = (detail.setup_steps ?? []).map((s) => normalizeStep({ ...s, phase: 'setup' }))
      teardownSteps.value = (detail.teardown_steps ?? []).map((s) => normalizeStep({ ...s, phase: 'teardown' }))
    }
    suiteCases.value = await listSuiteCases(id)
    suiteVars.value = await listVariables({ scope: 'suite', suite_id: id })
    varEditing.value = null
    stepsLoaded = true
  } finally {
    loadingDetail.value = false
  }
}

// 切换套件时若有未保存的步骤改动，先确认
async function confirmDiscardSteps(): Promise<boolean> {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      '当前套件的步骤尚未保存，切换后这些修改将丢失。',
      '未保存的修改',
      { type: 'warning', confirmButtonText: '仍然切换', cancelButtonText: '留在当前' },
    )
    return true
  } catch {
    return false
  }
}

async function onSelectSuite(s: Suite) {
  if (s.id === activeSuite.value) return
  const ok = await confirmDiscardSteps()
  if (!ok) return
  await selectSuite(s.id)
}

/* ============ 套件信息保存 / 删除 ============ */
function openCreate() {
  editingId.value = null
  form.value = { name: '', description: '' }
  dialogVisible.value = true
}

function openEdit(suite: Suite) {
  editingId.value = suite.id
  form.value = { name: suite.name, description: suite.description ?? '' }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入套件名称')
    return
  }
  savingSuite.value = true
  try {
    if (editingId.value) {
      const updated = await updateSuite(editingId.value, {
        name: form.value.name.trim(),
        description: form.value.description || null,
      })
      suiteDetail.value = {
        ...suiteDetail.value,
        ...updated,
        setup_steps: setupSteps.value,
        teardown_steps: teardownSteps.value,
      }
    } else {
      const suite = await createSuite(projectId, {
        name: form.value.name.trim(),
        description: form.value.description || undefined,
      })
      activeSuite.value = suite.id
      await selectSuite(suite.id)
    }
    dialogVisible.value = false
    await loadSuites()
  } finally {
    savingSuite.value = false
  }
}

async function saveSuiteSteps() {
  if (!activeSuite.value || !suiteDetail.value) return
  const allSteps = [...setupSteps.value, ...teardownSteps.value]
  for (const step of allSteps) {
    const msg = validateStep(step)
    if (msg) {
      ElMessage.warning(`套件步骤：${msg}`)
      return
    }
  }
  savingSteps.value = true
  try {
    const updated = await updateSuite(activeSuite.value, {
      name: suiteDetail.value.name,
      description: suiteDetail.value.description ?? null,
      setup_steps: setupSteps.value,
      teardown_steps: teardownSteps.value,
    })
    // 回填最新保存的步骤，保证「放弃改动」仍能回到已保存状态
    suiteDetail.value = {
      ...suiteDetail.value,
      ...updated,
      setup_steps: setupSteps.value,
      teardown_steps: teardownSteps.value,
    }
    markSaved()
    ElMessage.success('套件配置已保存')
    await loadSuites()
  } finally {
    savingSteps.value = false
  }
}

function discardSteps() {
  if (!suiteDetail.value) return
  setupSteps.value = (suiteDetail.value.setup_steps ?? []).map((s) => normalizeStep({ ...s, phase: 'setup' }))
  teardownSteps.value = (suiteDetail.value.teardown_steps ?? []).map((s) => normalizeStep({ ...s, phase: 'teardown' }))
  markSaved()
  ElMessage.info('已放弃未保存的步骤改动')
}

async function remove(suite: Suite) {
  try {
    await ElMessageBox.confirm(
      `确认删除套件「${suite.name}」？该套件的用例编排将被一并删除，此操作不可撤销。`,
      '删除套件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deleteSuite(suite.id)
  ElMessage.success('套件已删除')
  if (activeSuite.value === suite.id) {
    activeSuite.value = null
    suiteDetail.value = null
    suiteCases.value = []
    setupSteps.value = []
    teardownSteps.value = []
    suiteVars.value = []
    markSaved()
  }
  await loadSuites()
}

function onSuiteMenu(cmd: string | number | object) {
  if (!suiteDetail.value) return
  const key = String(cmd)
  if (key === 'edit') openEdit(suiteDetail.value)
  else if (key === 'delete') void remove(suiteDetail.value)
}

function onItemMenu(cmd: string | number | object, s: Suite) {
  const key = String(cmd)
  if (key === 'edit') openEdit(s)
  else if (key === 'delete') void remove(s)
}

/* ============ 用例编排 ============ */
async function openAddCase() {
  if (!activeSuite.value) return
  const suiteId = activeSuite.value
  resetAddDialog()
  allCases.value = []
  addDialogVisible.value = true
  loadingAddCases.value = true
  try {
    // 列表接口单页上限为 200；选择窗口必须能覆盖项目中的全部用例。
    const pageSize = 200
    const firstPage = await listCases(projectId, { page: 1, page_size: pageSize })
    const totalPages = Math.ceil(firstPage.total / pageSize)
    const remainingPages = await Promise.all(
      Array.from({ length: Math.max(0, totalPages - 1) }, (_, index) =>
        listCases(projectId, { page: index + 2, page_size: pageSize }),
      ),
    )
    if (activeSuite.value !== suiteId) return
    const inSuite = new Set(suiteCases.value.map((c) => c.case_id))
    allCases.value = [firstPage, ...remainingPages]
      .flatMap((page) => page.items)
      .filter((c) => !inSuite.has(c.id))
      .map((c) => ({ id: c.id, name: c.name, module_name: c.module_name ?? null, status: c.status }))
  } catch {
    ElMessage.error('加载可添加用例失败，请重试')
  } finally {
    loadingAddCases.value = false
  }
}

function resetAddDialog() {
  addKeyword.value = ''
  selectedIds.value = new Set()
}

function toggleSelect(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function groupSelectionState(group: CaseGroup) {
  return getGroupSelectionState(selectedIds.value, group.cases)
}

function toggleGroupSelection(group: CaseGroup, selected: boolean) {
  selectedIds.value = setGroupSelection(selectedIds.value, group.cases, selected)
}

async function addSelectedCases() {
  if (!activeSuite.value || selectedIds.value.size === 0) return
  addingCases.value = true
  try {
    await addSuiteCases(activeSuite.value, [...selectedIds.value])
    const count = selectedIds.value.size
    ElMessage.success(`已添加 ${count} 个用例`)
    addDialogVisible.value = false
    resetAddDialog()
    await selectSuite(activeSuite.value, { preserveSteps: true })
    await loadSuites()
  } finally {
    addingCases.value = false
  }
}

async function removeCase(suiteCase: SuiteCase) {
  if (!activeSuite.value) return
  try {
    await ElMessageBox.confirm(
      `确认从套件中移除用例「${suiteCase.case_name}」？`,
      '移除用例',
      { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await removeSuiteCase(activeSuite.value, suiteCase.case_id)
  ElMessage.success('已移除用例')
  await selectSuite(activeSuite.value, { preserveSteps: true })
  await loadSuites()
}

async function onReorder() {
  if (!activeSuite.value) return
  await reorderSuiteCases(activeSuite.value, suiteCases.value.map((c) => c.case_id))
  ElMessage.success('用例顺序已保存')
}

/* ============ 套件变量 ============ */
function startEditVar(v: Variable) {
  varEditing.value = { id: v.id, value: v.value }
}

async function saveVarValue(v: Variable) {
  if (!varEditing.value || varEditing.value.id !== v.id) return
  savingVar.value = true
  try {
    await updateVariable(v.id, { value: varEditing.value.value })
    varEditing.value = null
    ElMessage.success('变量已更新')
    await selectSuite(activeSuite.value!, { preserveSteps: true })
  } finally {
    savingVar.value = false
  }
}

async function removeVar(v: Variable) {
  try {
    await ElMessageBox.confirm(`确认删除变量「${v.name}」？`, '删除变量', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await deleteVariable(v.id)
  ElMessage.success('变量已删除')
  await selectSuite(activeSuite.value!, { preserveSteps: true })
}

onMounted(loadSuites)
</script>

<template>
  <el-row :gutter="16" class="suite-page">
    <!-- 左：套件列表 -->
    <el-col :xs="24" :lg="9" class="suite-col">
      <aside class="suite-sidebar">
        <div class="sidebar-head">
          <div class="sidebar-title-wrap">
            <h2 class="v2-card-title">套件管理</h2>
            <span class="sidebar-count v2-aux">{{ suites.length }} 个</span>
          </div>
          <el-button type="primary" :icon="Plus" @click="openCreate">新建套件</el-button>
        </div>

        <div class="sidebar-filter">
          <el-input v-model="keyword" placeholder="按名称或描述搜索" clearable class="sidebar-search">
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
          <el-select v-model="sortBy" class="sidebar-sort">
            <el-option label="最近更新" value="updated" />
            <el-option label="按名称" value="name" />
            <el-option label="用例数最多" value="cases" />
          </el-select>
        </div>

        <div v-loading="loadingSuites" class="suite-list">
          <div
            v-for="s in filteredSuites"
            :key="s.id"
            class="suite-item"
            :class="{ active: s.id === activeSuite }"
            @click="onSelectSuite(s)"
          >
            <div class="suite-item-top">
              <span class="suite-name" :title="s.name">{{ s.name }}</span>
              <el-tag :type="suiteStatusMeta(s.status).type" size="small" effect="plain">
                {{ suiteStatusMeta(s.status).label }}
              </el-tag>
            </div>
            <div class="suite-desc" :title="s.description ?? ''">{{ s.description || '暂无描述' }}</div>
            <div class="suite-item-foot">
              <span class="suite-foot-meta">
                <span class="meta-count">{{ s.case_count }} 个用例</span>
                <span class="meta-time">{{ formatDateTime(s.updated_at) }}</span>
              </span>
              <span class="suite-actions" @click.stop>
                <RunButton :type="'suite'" :id="s.id" :name="s.name" />
                <el-dropdown trigger="click" @command="onItemMenu($event, s)">
                  <el-button :icon="MoreFilled" text size="small" class="item-more" aria-label="更多操作" />
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="edit">编辑信息</el-dropdown-item>
                      <el-dropdown-item command="delete" divided>删除套件</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </span>
            </div>
          </div>

          <template v-if="filteredSuites.length === 0">
            <EmptyState
              v-if="suites.length === 0"
              title="还没创建套件"
              description="套件用于批量编排用例，并可配置前后置步骤与变量。"
              action-label="新建套件"
              @action="openCreate"
            />
            <div v-else class="no-match v2-aux">没有找到与「{{ keyword }}」匹配的套件</div>
          </template>
        </div>
      </aside>
    </el-col>

    <!-- 右：套件详情 -->
    <el-col :xs="24" :lg="15" class="suite-col">
      <template v-if="activeSuite && suiteDetail">
        <div v-loading="loadingDetail" class="detail-stack">
          <PageHeader :title="suiteDetail.name" :description="suiteDetail.description ?? ''">
            <RunButton :type="'suite'" :id="suiteDetail.id" :name="suiteDetail.name" />
            <el-dropdown trigger="click" @command="onSuiteMenu">
              <el-button :icon="MoreFilled">套件操作</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit">编辑信息</el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除套件</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </PageHeader>

          <!-- 统计胶囊 -->
          <div class="stat-pills">
            <div class="stat-pill">
              <span class="stat-value">{{ suiteCases.length }}</span>
              <span class="stat-label">用例</span>
            </div>
            <div class="stat-pill">
              <span class="stat-value">{{ setupSteps.length }}</span>
              <span class="stat-label">前置步骤</span>
            </div>
            <div class="stat-pill">
              <span class="stat-value">{{ teardownSteps.length }}</span>
              <span class="stat-label">后置步骤</span>
            </div>
            <div class="stat-pill">
              <span class="stat-value">{{ suiteVars.length }}</span>
              <span class="stat-label">变量</span>
            </div>
          </div>

          <!-- 用例编排（即时自动保存） -->
          <section class="detail-section">
            <header class="section-head">
              <div class="section-title-wrap">
                <span class="section-accent accent-indigo"></span>
                <div>
                  <div class="v2-card-title">用例编排</div>
                  <div class="v2-aux">拖拽调整执行顺序；添加与移除即时保存</div>
                </div>
              </div>
              <div class="section-head-right">
                <el-tag size="small" effect="plain" type="success">自动保存</el-tag>
                <el-button type="primary" :icon="Plus" @click="openAddCase">添加用例</el-button>
              </div>
            </header>

            <div class="section-body">
              <Draggable
                v-model="suiteCases"
                item-key="id"
                handle=".drag-handle"
                ghost-class="case-ghost"
                class="case-list"
                @end="onReorder"
              >
                <template #item="{ element, index }">
                  <div class="case-card">
                    <div class="case-row">
                      <span class="drag-handle" title="拖拽排序">⠿</span>
                      <span class="case-order">{{ index + 1 }}</span>
                      <span class="case-name" :title="element.case_name">{{ element.case_name }}</span>
                      <el-tag v-if="element.module_name" size="small" type="info" effect="plain" class="case-module">
                        {{ element.module_name }}
                      </el-tag>
                      <el-button class="case-remove" size="small" text type="danger" @click="removeCase(element)">
                        移除
                      </el-button>
                    </div>
                  </div>
                </template>
              </Draggable>

              <div v-if="suiteCases.length === 0" class="case-empty">
                <EmptyState
                  title="套件还没有用例"
                  description="从用例库中添加用例，拖拽即可调整执行顺序。"
                  action-label="添加用例"
                  @action="openAddCase"
                />
              </div>
            </div>
          </section>

          <!-- 执行步骤（显式保存） -->
          <section class="detail-section">
            <header class="section-head">
              <div class="section-title-wrap">
                <span class="section-accent accent-amber"></span>
                <div>
                  <div class="v2-card-title">执行步骤</div>
                  <div class="v2-aux">套件级前置 / 后置操作，点击「保存」后生效</div>
                </div>
              </div>
              <div class="section-head-right">
                <el-tag v-if="!dirty" size="small" effect="plain" type="info">需手动保存</el-tag>
                <el-tag v-else size="small" effect="light" type="warning">未保存</el-tag>
              </div>
            </header>

            <div class="steps-grid">
              <CaseStepEditor
                :model-value="setupSteps"
                @update:model-value="onSetupStepsChange"
                phase="setup"
                title="前置操作"
                description="运行时在套件内每个用例主体之前执行"
                tone="warning"
              />
              <CaseStepEditor
                :model-value="teardownSteps"
                @update:model-value="onTeardownStepsChange"
                phase="teardown"
                title="后置操作"
                description="运行时在套件内每个用例完成后执行；失败时仍会尝试清理"
                tone="success"
              />
            </div>

            <transition name="fade">
              <div v-if="dirty" class="steps-save-bar">
                <div class="save-hint">
                  <span class="save-dot"></span>
                  有未保存的步骤改动
                </div>
                <div class="save-actions">
                  <el-button @click="discardSteps">放弃改动</el-button>
                  <el-button type="primary" :loading="savingSteps" @click="saveSuiteSteps">保存套件配置</el-button>
                </div>
              </div>
            </transition>
          </section>

          <!-- 套件变量（即时保存） -->
          <section class="detail-section">
            <header class="section-head">
              <div class="section-title-wrap">
                <span class="section-accent accent-violet"></span>
                <div>
                  <div class="v2-card-title">套件变量</div>
                  <div class="v2-aux">执行时注入的键值对，可随时编辑、删除</div>
                </div>
              </div>
              <div class="section-head-right">
                <el-tag size="small" effect="plain" type="success">即时保存</el-tag>
              </div>
            </header>

            <div class="section-body">
              <div v-if="suiteVars.length === 0" class="var-empty v2-aux">暂无套件变量</div>
              <div v-else class="var-list">
                <div v-for="v in suiteVars" :key="v.id" class="var-row">
                  <span class="var-name">{{ v.name }}</span>
                  <el-input
                    v-if="varEditing?.id === v.id"
                    v-model="varEditing.value"
                    size="small"
                    class="var-input"
                    @keyup.enter="saveVarValue(v)"
                    @blur="saveVarValue(v)"
                  />
                  <span v-else class="var-value" :title="v.value">{{ v.value || '—' }}</span>
                  <span class="var-actions">
                    <el-button
                      v-if="varEditing?.id === v.id"
                      size="small"
                      type="primary"
                      text
                      :loading="savingVar"
                      @click="saveVarValue(v)"
                    >
                      保存
                    </el-button>
                    <el-button v-else size="small" text @click="startEditVar(v)">编辑</el-button>
                    <el-button size="small" type="danger" text @click="removeVar(v)">删除</el-button>
                  </span>
                </div>
              </div>
            </div>
          </section>
        </div>
      </template>

      <div v-else-if="loadingSuites || loadingDetail" v-loading="true" class="detail-loading" />
      <el-empty v-else class="detail-empty" description="请选择或新建一个套件">
        <el-button type="primary" @click="openCreate">新建套件</el-button>
      </el-empty>
    </el-col>
  </el-row>

  <!-- 新建 / 编辑套件 -->
  <el-dialog v-model="dialogVisible" :title="editingId ? '编辑套件' : '新建套件'" width="480px" append-to-body>
    <el-form label-width="80px" @submit.prevent="save">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="请输入套件名称" maxlength="255" @keyup.enter="save" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="form.description" type="textarea" :rows="3" placeholder="可选" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingSuite" @click="save">保存</el-button>
    </template>
  </el-dialog>

  <!-- 添加用例：按模块分组 + 搜索 + 多选批量 -->
  <el-dialog v-model="addDialogVisible" title="添加用例" width="560px" append-to-body class="add-case-dialog">
    <el-input v-model="addKeyword" placeholder="搜索用例名称" clearable class="add-case-search">
      <template #prefix>
        <el-icon><Search /></el-icon>
      </template>
    </el-input>
    <div class="add-case-hint v2-aux">从用例库挑选用例，可多选批量添加。</div>

    <div v-loading="loadingAddCases" class="add-case-body">
      <template v-if="groupedCases.length">
        <div v-for="group in groupedCases" :key="group.name" class="case-group">
          <div class="case-group-head">
            <span>{{ group.name }}</span>
            <span class="case-group-actions">
              <span class="case-group-count">{{ group.cases.length }} 个</span>
              <el-checkbox
                :model-value="groupSelectionState(group).checked"
                :indeterminate="groupSelectionState(group).indeterminate"
                @click.stop
                @change="toggleGroupSelection(group, Boolean($event))"
              >
                全选
              </el-checkbox>
            </span>
          </div>
          <div
            v-for="c in group.cases"
            :key="c.id"
            class="case-pick-row"
            :class="{ selected: selectedIds.has(c.id) }"
            role="checkbox"
            :aria-checked="selectedIds.has(c.id)"
            tabindex="0"
            @click="toggleSelect(c.id)"
            @keyup.enter="toggleSelect(c.id)"
          >
            <span class="pick-check" :class="{ on: selectedIds.has(c.id) }">
              {{ selectedIds.has(c.id) ? '✓' : '' }}
            </span>
            <span class="case-pick-name" :title="c.name">{{ c.name }}</span>
            <el-tag v-if="c.status !== 'active'" :type="caseStatusMeta(c.status).type" size="small" effect="light">
              {{ caseStatusMeta(c.status).label }}
            </el-tag>
          </div>
        </div>
      </template>
      <div v-else class="add-case-empty v2-aux">
        {{ loadingAddCases ? '正在加载用例…' : allCases.length ? '没有匹配的用例' : '该套件已加入全部可用用例' }}
      </div>
    </div>

    <template #footer>
      <span class="add-case-count">
        {{ selectedIds.size ? `已选 ${selectedIds.size} 个` : '' }}
      </span>
      <span class="add-case-footer-actions">
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="addingCases"
          :disabled="selectedIds.size === 0"
          @click="addSelectedCases"
        >
          添加({{ selectedIds.size }})
        </el-button>
      </span>
    </template>
  </el-dialog>
</template>

<style scoped>
/* ============ 左侧：套件列表 ============ */
.suite-sidebar {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 120px);
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  overflow: hidden;
}
.sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--border);
}
.sidebar-title-wrap {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sidebar-count {
  font-size: 12px;
}
.sidebar-filter {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.sidebar-search {
  flex: 1;
}
.sidebar-sort {
  width: 124px;
  flex-shrink: 0;
}
.suite-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px;
}
.suite-item {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 10px;
  cursor: pointer;
  background: #fff;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.suite-item:hover {
  border-color: var(--primary);
  box-shadow: 0 2px 8px rgba(79, 70, 229, 0.08);
}
.suite-item.active {
  border-color: var(--primary);
  background: var(--primary-light);
}
.suite-item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.suite-name {
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.suite-desc {
  margin: 6px 0 10px;
  color: var(--text-2);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.suite-item-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.suite-foot-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-2);
  font-size: 12px;
  min-width: 0;
}
.meta-time {
  white-space: nowrap;
}
.suite-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.item-more {
  padding: 2px;
}
.no-match {
  text-align: center;
  padding: 24px 12px;
}

/* ============ 右侧：套件详情 ============ */
.detail-stack {
  width: 100%;
}
.detail-section {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  margin-bottom: 16px;
  overflow: visible;
}
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.section-title-wrap {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.section-accent {
  width: 4px;
  height: 18px;
  border-radius: 2px;
  flex-shrink: 0;
  margin-top: 2px;
}
.accent-indigo {
  background: var(--primary);
}
.accent-amber {
  background: var(--warning);
}
.accent-violet {
  background: #8b5cf6;
}
.section-head-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.section-body {
  padding: 16px 20px;
}

/* 统计胶囊 */
.stat-pills {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.stat-pill {
  flex: 1;
  min-width: 96px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat-value {
  font-size: var(--font-kpi);
  line-height: 1;
  font-weight: 700;
  color: var(--text);
}
.stat-label {
  color: var(--text-2);
  font-size: 12px;
}

/* 用例编排 */
.case-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.case-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.case-card:hover {
  border-color: var(--primary);
  background: var(--primary-light);
  box-shadow: 0 2px 8px rgba(79, 70, 229, 0.08);
}
.case-ghost {
  opacity: 0.4;
  background: var(--primary-light);
  border: 1px dashed var(--primary);
}
.case-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
}
.drag-handle {
  cursor: move;
  color: #999;
  flex-shrink: 0;
}
.drag-handle:hover {
  color: var(--primary);
}
.case-order {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
  background: var(--primary-light);
  color: var(--primary);
}
.case-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.case-module {
  flex-shrink: 0;
}
.case-remove {
  flex-shrink: 0;
}
.case-empty {
  padding: 4px 0;
}

/* 执行步骤 */
.steps-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 16px;
  padding: 16px 20px 0;
}
@media (min-width: 1600px) {
  .steps-grid {
    grid-template-columns: 1fr 1fr;
  }
}
.steps-save-bar {
  position: sticky;
  bottom: 0;
  margin-top: 16px;
  padding: 12px 20px;
  background: var(--card-bg);
  border-top: 1px solid var(--border);
  box-shadow: 0 -4px 16px rgba(15, 23, 42, 0.06);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.save-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--warning);
  font-size: 13px;
}
.save-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--warning);
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.2);
}
.save-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 套件变量 */
.var-list {
  display: flex;
  flex-direction: column;
}
.var-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.var-row:last-child {
  border-bottom: none;
}
.var-name {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  min-width: 120px;
  color: var(--text);
}
.var-value {
  color: var(--text-2);
  min-width: 160px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.var-input {
  width: 240px;
  max-width: 100%;
}
.var-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  margin-left: auto;
}
.var-empty {
  text-align: center;
  padding: 24px 0;
}

/* 详情加载 / 空态 */
.detail-loading {
  min-height: 220px;
}
.detail-empty {
  padding-top: 80px;
}

/* 动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

/* 添加用例弹窗 */
.add-case-search {
  width: 100%;
}
.add-case-hint {
  margin: 10px 0 12px;
}
.add-case-body {
  max-height: 360px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.case-group + .case-group {
  border-top: 1px solid var(--border);
}
.case-group-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f8fafc;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.case-group-count {
  color: var(--text-2);
  font-weight: 400;
  font-size: 12px;
}
.case-group-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 400;
}
.case-group-actions :deep(.el-checkbox) {
  height: auto;
  margin-right: 0;
}
.case-pick-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  cursor: pointer;
  border-top: 1px solid var(--border);
  transition: background 0.12s;
}
.case-pick-row:first-of-type {
  border-top: none;
}
.case-pick-row:hover {
  background: var(--primary-light);
}
.case-pick-row.selected {
  background: var(--primary-light);
}
.case-pick-row.selected .pick-check {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
.pick-check {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--border);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: transparent;
  flex-shrink: 0;
  transition: all 0.12s;
}
.case-pick-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.add-case-empty {
  text-align: center;
  padding: 32px 12px;
}
.add-case-count {
  font-size: 13px;
  color: var(--text-2);
}
.add-case-footer-actions {
  display: flex;
  gap: 8px;
}

/* ============ 响应式：中宽以下列表与详情堆叠 ============ */
@media (max-width: 1199px) {
  .suite-sidebar {
    position: static;
    max-height: none;
  }
  .suite-list {
    overflow: visible;
    max-height: none;
  }
  .suite-col + .suite-col {
    margin-top: 16px;
  }
}
</style>
