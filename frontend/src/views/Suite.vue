<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, reactive, ref, watch, type ComponentPublicInstance } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MoreFilled, Plus, Search } from '@element-plus/icons-vue'
import Draggable from 'vuedraggable'

import {
  createSuite,
  createVariable,
  deleteSuite,
  getSuiteCaseVariables,
  patchSuiteCaseVariables,
  updateSuite,
  type Suite,
  type SuiteCase,
  type SuiteCaseVariables,
} from '@/api/suites'
import { listModules, type TestModule } from '@/api/modules'
import CaseVariableEditor, { type VariableSavePayload } from '@/components/CaseVariableEditor.vue'
import CaseVariableSummary from '@/components/CaseVariableSummary.vue'
import EmptyState from '@/components/EmptyState.vue'
import ModuleTree from '@/components/ModuleTree.vue'
import RunButton from '@/components/RunButton.vue'
import SuiteCasePicker from '@/components/SuiteCasePicker.vue'
import SuiteStepSection from '@/components/SuiteStepSection.vue'
import { useSuiteCases } from '@/composables/useSuiteCases'
import { useSuiteDetail } from '@/composables/useSuiteDetail'
import { useSuiteList } from '@/composables/useSuiteList'
import { usePermission } from '@/composables/usePermission'
import { useProjectContextStore } from '@/stores/projectContext'
import { membershipVariablesToPreview, type EditorVariable } from '@/utils/caseVariables'
import { formatDateTime } from '@/utils/format'
import { moduleFilterParams, parseModuleKey, type ModuleKey } from '@/utils/moduleFilter'
import { parseSuiteId } from '@/utils/suiteNavigation'
import { digitsOnly } from '@/utils/suiteCaseOrder'

const route = useRoute()
const router = useRouter()
const projectId = Number(route.params.projectId)
const projectContext = useProjectContextStore()
const { canWriteAssets } = usePermission()

// 三个 composable 通过这个编排回调串起“选中套件 → 加载详情和用例”的流程。
let loadSuiteData: (id: number) => Promise<void> = async () => undefined
const suiteList = useSuiteList(projectId, (id) => loadSuiteData(id))
const suiteDetailState = useSuiteDetail(() => suiteList.loadSuites())
const suiteCaseState = useSuiteCases(projectId, suiteList.activeSuite, async (preserveSteps = false) => {
  if (suiteList.activeSuite.value) {
    await suiteDetailState.selectSuite(suiteList.activeSuite.value, { preserveSteps })
  }
})
loadSuiteData = async (id) => {
  await suiteDetailState.selectSuite(id)
  await suiteCaseState.loadSuiteCases(id)
}

const {
  suites,
  loadingSuites,
  activeSuite,
  keyword,
  sortBy,
  truncated,
  filteredSuites,
  suiteStatusMeta,
  selectSuite,
  setModuleKey,
  loadSuites,
  clearActiveSuite,
} = suiteList

// 模块树筛选：三态 key 由 ModuleTree 维护，套件列表走服务端过滤
const selectedModule = ref<ModuleKey>('all')
let keywordTimer: ReturnType<typeof setTimeout> | null = null

async function onModuleSelect(key: string) {
  // 切换模块会换掉当前套件；未保存的步骤先确认，避免静默丢失
  if (!(await confirmDiscardSteps())) return
  selectedModule.value = parseModuleKey(key)
  await setModuleKey(selectedModule.value)
}

function onKeywordInput() {
  // 关键字在服务端过滤（覆盖全部套件，而不是仅已加载的一页），做 300ms 防抖
  if (keywordTimer) clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => {
    void loadSuites()
  }, 300)
}

onUnmounted(() => {
  if (keywordTimer) clearTimeout(keywordTimer)
})
const {
  loadingDetail,
  suiteDetail,
  setupSteps,
  teardownSteps,
  savingSteps,
  dirty,
  suiteVars,
  varEditing,
  savingVar,
  onSetupStepsChange,
  onTeardownStepsChange,
  confirmDiscardSteps,
  saveSuiteSteps,
  discardSteps,
  startEditVar,
  saveVarValue,
  removeVar,
  reset: resetDetail,
} = suiteDetailState
const {
  suiteCases,
  addDialogVisible,
  addKeyword,
  allCases,
  selectedIds,
  addingCases,
  loadingAddCases,
  groupedCases,
  toggleSelect,
  groupSelectionState,
  toggleGroupSelection,
  isCaseGroupCollapsed,
  toggleCaseGroup,
  caseStatusMeta,
  openAddCase,
  addSelectedCases,
  removeCase,
  onReorder,
  moveCaseToPosition,
  ordering,
  casesRefreshVersion,
} = suiteCaseState

const editingOrderMembershipId = ref<number | null>(null)
const editingOrderValue = ref('')
const orderEditSignature = ref('')
type OrderInputInstance = { focus: () => void; select: () => void }
const orderInput = ref<OrderInputInstance | null>(null)
function setOrderInputRef(instance: Element | ComponentPublicInstance | null) {
  const candidate = instance as Partial<OrderInputInstance> | null
  orderInput.value = candidate && typeof candidate.focus === 'function' && typeof candidate.select === 'function'
    ? candidate as OrderInputInstance
    : null
}
let orderEditSubmitting = false

function beginOrderEdit(item: SuiteCase, event: Event) {
  event.stopPropagation()
  if (!canWriteAssets || ordering.value) return
  editingOrderMembershipId.value = item.id
  editingOrderValue.value = String(suiteCases.value.findIndex((candidate) => candidate.id === item.id) + 1)
  orderEditSignature.value = suiteCases.value.map((candidate) => candidate.id).join(',')
  void nextTick(() => {
    orderInput.value?.focus()
    orderInput.value?.select()
  })
}

function cleanOrderInput(value: string) {
  editingOrderValue.value = digitsOnly(value)
}

function cancelOrderEdit() {
  editingOrderMembershipId.value = null
  editingOrderValue.value = ''
  orderEditSubmitting = false
}

async function submitOrderEdit() {
  if (orderEditSubmitting || editingOrderMembershipId.value === null) return
  orderEditSubmitting = true
  const raw = editingOrderValue.value
  const position = Number(raw)
  if (!raw || !Number.isInteger(position) || position < 1 || position > suiteCases.value.length) {
    ElMessage.warning(`请输入 1-${suiteCases.value.length} 的数字`)
    cancelOrderEdit()
    return
  }
  await moveCaseToPosition(editingOrderMembershipId.value, position)
  // onReorder 负责成功提示；这里仅结束编辑，避免 Enter 后 blur 重复提示。
  cancelOrderEdit()
}

function onOrderKeydown(event: Event | KeyboardEvent) {
  event.stopPropagation()
  if (!('key' in event)) return
  if (event.key === 'Escape') cancelOrderEdit()
  else if (event.key === 'Enter') void submitOrderEdit()
}

watch(activeSuite, cancelOrderEdit)
watch(casesRefreshVersion, cancelOrderEdit)
watch(() => suiteCases.value.map((item) => item.id).join(','), (signature) => {
  if (editingOrderMembershipId.value !== null && signature !== orderEditSignature.value) cancelOrderEdit()
})

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
// module_id: 0 表示未分组（Element Plus 的 el-option 不接受 null 作为 value）
const form = ref<{ name: string; description: string; module_id: number }>({
  name: '',
  description: '',
  module_id: 0,
})
/** 对话框里的模块下拉：每次打开时拉一次，保证与左侧树同步 */
const moduleOptions = ref<TestModule[]>([])
const savingSuite = ref(false)
const variableDialogVisible = ref(false)
const variableForm = ref({ name: '', value: '', description: '', is_sensitive: false })
const savingNewVariable = ref(false)

async function onSelectSuite(suite: Suite) {
  if (suite.id === activeSuite.value) return
  if (!(await confirmDiscardSteps())) return
  await selectSuite(suite.id)
}

function openCaseEditor(suiteCase: SuiteCase) {
  const suiteId = activeSuite.value
  if (!suiteId) return
  void router.push({
    path: `/projects/${projectId}/cases/${suiteCase.case_id}/edit`,
    query: {
      return_to: 'suite',
      suite_id: String(suiteId),
    },
  })
}

// ---------- 用例变量快捷展示与编排项覆盖 ----------
const variableEditor = reactive({
  visible: false,
  loading: false,
  saving: false,
  membership: null as SuiteCase | null,
  variables: [] as EditorVariable[],
})

/** 保存后只刷新当前编排项摘要，不重置滚动位置、排序或展开状态。 */
function applyMembershipPreview(membershipId: number, detail: SuiteCaseVariables) {
  const index = suiteCases.value.findIndex((item) => item.id === membershipId)
  if (index === -1) return
  const preview = membershipVariablesToPreview(detail.variables)
  const next = [...suiteCases.value]
  next[index] = { ...next[index], variable_count: detail.total, variables_preview: preview }
  suiteCases.value = next
}

async function openVariableEditor(item: SuiteCase) {
  const suiteId = activeSuite.value
  if (!suiteId) return
  variableEditor.membership = item
  variableEditor.variables = []
  variableEditor.visible = true
  variableEditor.loading = true
  try {
    const detail = await getSuiteCaseVariables(suiteId, item.id)
    variableEditor.variables = detail.variables.map((variable) => ({ ...variable }))
  } catch (error) {
    ElMessage.error((error as Error).message || '加载变量详情失败')
    variableEditor.visible = false
  } finally {
    variableEditor.loading = false
  }
}

async function saveVariableOverrides(payload: VariableSavePayload) {
  const suiteId = activeSuite.value
  const membership = variableEditor.membership
  if (!suiteId || !membership || payload.kind !== 'occurrence') return
  variableEditor.saving = true
  try {
    const detail = await patchSuiteCaseVariables(
      suiteId,
      membership.id,
      payload.updates as Record<string, string | null>,
    )
    applyMembershipPreview(membership.id, detail)
    ElMessage.success('编排项变量覆盖已保存')
    variableEditor.visible = false
  } catch (error) {
    ElMessage.error((error as Error).message || '保存失败')
  } finally {
    variableEditor.saving = false
  }
}

async function loadModuleOptions() {
  try {
    moduleOptions.value = await listModules(projectId, { scope: 'suite' })
  } catch {
    // 模块下拉只是便利项，拉取失败不阻塞套件的新建/编辑
    moduleOptions.value = []
  }
}

/** 扁平模块列表 → 「父 / 子」路径标签，避免同名子模块在扁平下拉里无法区分 */
function moduleOptionLabel(module: TestModule): string {
  const byId = new Map(moduleOptions.value.map((item) => [item.id, item]))
  const parts = [module.name]
  let parentId = module.parent_id
  const seen = new Set<number>([module.id])
  while (parentId != null && !seen.has(parentId)) {
    const parent = byId.get(parentId)
    if (!parent) break
    seen.add(parent.id)
    parts.unshift(parent.name)
    parentId = parent.parent_id
  }
  return parts.join(' / ')
}

async function openCreate() {
  editingId.value = null
  form.value = {
    name: '',
    description: '',
    // 默认落在当前选中的模块下；「全部/未分组」时保持未分组
    module_id: moduleFilterParams(selectedModule.value).module_id ?? 0,
  }
  await loadModuleOptions()
  dialogVisible.value = true
}

async function openEdit(suite: Suite) {
  editingId.value = suite.id
  form.value = {
    name: suite.name,
    description: suite.description ?? '',
    module_id: suite.module_id ?? 0,
  }
  await loadModuleOptions()
  dialogVisible.value = true
}

function openCreateVariable() {
  variableForm.value = { name: '', value: '', description: '', is_sensitive: false }
  variableDialogVisible.value = true
}

async function saveNewVariable() {
  const suiteId = activeSuite.value
  const name = variableForm.value.name.trim()
  if (!suiteId) return
  if (!name) {
    ElMessage.warning('请输入变量名')
    return
  }
  savingNewVariable.value = true
  try {
    await createVariable({
      scope: 'suite',
      suite_id: suiteId,
      name,
      value: variableForm.value.value,
      description: variableForm.value.description.trim() || null,
      is_sensitive: variableForm.value.is_sensitive,
    })
    variableDialogVisible.value = false
    ElMessage.success('变量已创建')
    await suiteDetailState.selectSuite(suiteId, { preserveSteps: true })
  } finally {
    savingNewVariable.value = false
  }
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
        module_id: form.value.module_id === 0 ? null : form.value.module_id,
      })
      if (suiteDetail.value) {
        suiteDetail.value = {
          ...suiteDetail.value,
          ...updated,
          setup_steps: setupSteps.value,
          teardown_steps: teardownSteps.value,
        }
      }
    } else {
      const suite = await createSuite(projectId, {
        name: form.value.name.trim(),
        description: form.value.description || undefined,
        module_id: form.value.module_id === 0 ? null : form.value.module_id,
      })
      await selectSuite(suite.id)
    }
    dialogVisible.value = false
    await loadSuites()
  } finally {
    savingSuite.value = false
  }
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
    clearActiveSuite()
    resetDetail()
    await suiteCaseState.loadSuiteCases(null)
  }
  await loadSuites()
}

function onItemMenu(cmd: string | number | object, suite: Suite) {
  const key = String(cmd)
  if (key === 'edit') openEdit(suite)
  else if (key === 'delete') void remove(suite)
}

async function initialize() {
  // 先加载项目角色，避免套件详情先返回后变量编辑入口因权限尚未解析而被隐藏。
  try {
    await projectContext.load(projectId)
  } catch {
    // 项目权限接口失败时仍让套件接口自行返回错误，避免产生未处理 Promise。
  }
  await loadSuites()
  const querySuiteId = parseSuiteId(route.query.suite_id)
  if (querySuiteId !== null && suites.value.some((suite) => suite.id === querySuiteId)) {
    await selectSuite(querySuiteId)
  }
}

onMounted(() => {
  void initialize()
})
</script>

<template>
  <div class="suite-layout">
    <!-- 左区：模块树 + 套件列表，两栏合计占页面宽度的 42% -->
    <div class="suite-left">
      <!-- 模块树 —— 与用例页共用同一组件与外观 -->
      <ModuleTree class="suite-tree" :project-id="projectId" scope="suite" title="套件模块" :selected-key="selectedModule"
        :writable="canWriteAssets" @select="onModuleSelect" @mutated="loadSuites" />

      <!-- 套件列表（标题区、搜索排序、卡片列表） -->
      <aside class="suite-sidebar">
        <div class="sidebar-head">
          <div class="sidebar-title-wrap">
            <h2 class="v2-card-title">套件管理</h2>
            <span class="sidebar-count v2-aux">{{ suites.length }} 个</span>
          </div>
          <el-button v-if="canWriteAssets" type="primary" :icon="Plus" @click="openCreate">新建套件</el-button>
        </div>
        <div class="sidebar-filter">
          <el-input v-model="keyword" placeholder="搜索套件" clearable class="sidebar-search" @input="onKeywordInput"
            @clear="loadSuites">
            <template #prefix><el-icon>
                <Search />
              </el-icon></template>
          </el-input>
          <el-select v-model="sortBy" class="sidebar-sort">
            <el-option label="最近更新" value="updated" />
            <el-option label="按名称" value="name" />
            <el-option label="用例数最多" value="cases" />
          </el-select>
        </div>
        <div v-loading="loadingSuites" class="suite-list">
          <div v-for="suite in filteredSuites" :key="suite.id" class="suite-item"
            :class="{ active: suite.id === activeSuite }" @click="onSelectSuite(suite)">
            <div class="suite-item-top">
              <span class="suite-name" :title="suite.name">{{ suite.name }}</span>
              <el-tag :type="suiteStatusMeta(suite.status).type" size="small" effect="plain">
                {{ suiteStatusMeta(suite.status).label }}
              </el-tag>
            </div>
            <div class="suite-desc" :title="suite.description ?? ''">{{ suite.description || '暂无描述' }}</div>
            <div class="suite-item-foot">
              <span class="suite-foot-meta">
                <span class="meta-count">{{ suite.case_count }} 个用例</span>
                <span class="meta-time">{{ formatDateTime(suite.updated_at) }}</span>
              </span>
              <span class="suite-actions" @click.stop>
                <RunButton :type="'suite'" :id="suite.id" :name="suite.name" />
                <el-dropdown v-if="canWriteAssets" trigger="click" @command="onItemMenu($event, suite)">
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
            <div v-if="keyword" class="no-match v2-aux">没有找到与「{{ keyword }}」匹配的套件</div>
            <EmptyState v-else :title="selectedModule === 'all' ? '还没创建套件' : '该模块下暂无套件'"
              description="套件用于批量编排用例，并可配置前后置步骤与变量。" :action-label="canWriteAssets ? '新建套件' : undefined"
              @action="openCreate" />
          </template>
          <div v-if="truncated" class="list-truncated v2-aux">
            套件较多，仅显示前 {{ suites.length }} 个，请用搜索缩小范围
          </div>
        </div>
      </aside>
    </div>

    <!-- 右：套件详情 -->
    <div class="suite-main">
      <template v-if="activeSuite && suiteDetail">
        <div v-loading="loadingDetail" class="detail-stack">
          <div class="stat-pills">
            <div class="stat-pill"><span class="stat-value">{{ suiteCases.length }}</span><span
                class="stat-label">用例</span>
            </div>
            <div class="stat-pill"><span class="stat-value">{{ setupSteps.length }}</span><span
                class="stat-label">前置步骤</span>
            </div>
            <div class="stat-pill"><span class="stat-value">{{ teardownSteps.length }}</span><span
                class="stat-label">后置步骤</span></div>
            <div class="stat-pill"><span class="stat-value">{{ suiteVars.length }}</span><span
                class="stat-label">变量</span>
            </div>
          </div>

          <section class="detail-section">
            <header class="section-head case-orchestration-head">
              <div class="section-title-wrap"><span class="section-accent accent-indigo"></span>
                <div>
                  <div class="v2-card-title">用例编排</div>
                  <div class="v2-aux">拖拽调整执行顺序；添加与移除即时保存</div>
                </div>
              </div>
              <div class="section-head-right"><el-tag size="small" effect="plain" type="success">自动保存</el-tag><el-button
                  v-if="canWriteAssets" type="primary" :icon="Plus" @click="openAddCase">添加用例</el-button></div>
            </header>
            <div class="section-body">
              <Draggable v-model="suiteCases" :disabled="!canWriteAssets || ordering" item-key="id"
                handle=".drag-handle" ghost-class="case-ghost" class="case-list" @end="onReorder">
                <template #item="{ element, index }">
                  <div class="case-card" @dblclick="openCaseEditor(element)">
                    <div class="case-row"><span class="drag-handle" title="拖拽排序">⠿</span>
                      <span v-if="editingOrderMembershipId !== element.id" class="case-order"
                        :class="{ editable: canWriteAssets }" title="点击设置编号"
                        @click.stop="beginOrderEdit(element, $event)"
                        @dblclick.stop="beginOrderEdit(element, $event)">{{ index + 1 }}</span>
                      <el-input v-else :ref="setOrderInputRef" v-model="editingOrderValue" class="case-order-input"
                        size="small" @click.stop @dblclick.stop @input="cleanOrderInput" @keydown="onOrderKeydown"
                        @blur="void submitOrderEdit()" />
                      <span class="case-name" :title="element.case_name">{{ element.case_name
                        }}</span>
                      <CaseVariableSummary class="case-variables" :variables="element.variables_preview ?? []"
                        :total="element.variable_count ?? 0" :readonly="!canWriteAssets"
                        @open="openVariableEditor(element)" />
                      <el-tag v-if="element.module_name" size="small" type="info" effect="plain"
                        class="case-module">{{ element.module_name }}</el-tag><el-button v-if="canWriteAssets" class="case-remove"
                        size="small" text type="danger" @click="removeCase(element)" @dblclick.stop>移除</el-button></div>
                  </div>
                </template>
              </Draggable>
              <div v-if="suiteCases.length === 0" class="case-empty">
                <EmptyState title="套件还没有用例" description="从用例库中添加用例，拖拽即可调整执行顺序。"
                  :action-label="canWriteAssets ? '添加用例' : undefined" @action="openAddCase" />
              </div>
            </div>
          </section>

          <SuiteStepSection :project-id="projectId" :setup-steps="setupSteps" :teardown-steps="teardownSteps"
            :readonly="!canWriteAssets" :dirty="dirty" :saving="savingSteps" @update:setup-steps="onSetupStepsChange"
            @update:teardown-steps="onTeardownStepsChange" @save="saveSuiteSteps(activeSuite)"
            @discard="discardSteps" />

          <section class="detail-section">
            <header class="section-head">
              <div class="section-title-wrap"><span class="section-accent accent-violet"></span>
                <div>
                  <div class="v2-card-title">套件变量</div>
                  <div class="v2-aux">执行时注入的键值对，可随时编辑、删除</div>
                </div>
              </div>
              <div class="section-head-right">
                <el-tag size="small" effect="plain" type="success">即时保存</el-tag>
                <el-button v-if="canWriteAssets" type="primary" :icon="Plus" size="small" @click="openCreateVariable">
                  新增变量
                </el-button>
              </div>
            </header>
            <div class="section-body">
              <div v-if="suiteVars.length === 0" class="var-empty v2-aux">暂无套件变量</div>
              <div v-else class="var-list">
                <div v-for="variable in suiteVars" :key="variable.id" class="var-row">
                  <span class="var-name">{{ variable.name }}</span>
                  <el-input v-if="varEditing?.id === variable.id" v-model="varEditing.value" size="small"
                    :type="varEditing.is_sensitive ? 'password' : 'text'" show-password autocomplete="new-password"
                    class="var-input" @input="varEditing.sensitive_value_changed = true" @keyup.enter="saveVarValue(variable, activeSuite)"
                    @blur="saveVarValue(variable, activeSuite)" />
                  <el-switch v-if="varEditing?.id === variable.id" v-model="varEditing.is_sensitive" size="small" active-text="敏感" />
                  <span v-else class="var-value" :title="variable.is_sensitive ? '敏感值不回显' : variable.value">{{ variable.is_sensitive ? '********' : (variable.value || '—') }}</span>
                  <span class="var-actions">
                    <el-button v-if="varEditing?.id === variable.id" size="small" type="primary" text
                      :loading="savingVar" @click="saveVarValue(variable, activeSuite)">保存</el-button>
                  <el-button v-if="canWriteAssets && varEditing?.id !== variable.id" size="small" text
                      @click="startEditVar(variable)">编辑</el-button>
                    <el-button v-if="canWriteAssets" size="small" type="danger" text
                      @click="removeVar(variable, activeSuite)">删除</el-button>
                  </span>
                </div>
              </div>
            </div>
          </section>
        </div>
      </template>
      <div v-else-if="loadingSuites || loadingDetail" v-loading="true" class="detail-loading" />
      <el-empty v-else class="detail-empty" description="请选择或新建一个套件"><el-button v-if="canWriteAssets" type="primary"
          @click="openCreate">新建套件</el-button></el-empty>
    </div>
  </div>

  <el-dialog v-model="dialogVisible" :title="editingId ? '编辑套件' : '新建套件'" width="480px" append-to-body>
    <el-form label-width="80px" @submit.prevent="save">
      <el-form-item label="名称" required><el-input v-model="form.name" placeholder="请输入套件名称" maxlength="255"
          @keyup.enter="save" /></el-form-item>
      <el-form-item label="模块">
        <el-select v-model="form.module_id" placeholder="未分组" class="suite-module-select">
          <el-option :value="0" label="未分组" />
          <el-option v-for="module in moduleOptions" :key="module.id" :label="moduleOptionLabel(module)"
            :value="module.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="3"
          placeholder="可选" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary"
        :loading="savingSuite" @click="save">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="variableDialogVisible" title="新增套件变量" width="480px" append-to-body>
    <el-form label-width="80px" @submit.prevent="saveNewVariable">
      <el-form-item label="变量名" required>
        <el-input v-model="variableForm.name" placeholder="如 username" maxlength="100" @keyup.enter="saveNewVariable" />
      </el-form-item>
      <el-form-item label="值">
        <el-input v-model="variableForm.value" :type="variableForm.is_sensitive ? 'password' : 'text'" show-password autocomplete="new-password" placeholder="请输入变量值" />
      </el-form-item>
      <el-form-item label="敏感值"><el-switch v-model="variableForm.is_sensitive" /><span class="scope-tip">敏感值不会回显</span></el-form-item>
      <el-form-item label="说明">
        <el-input v-model="variableForm.description" type="textarea" :rows="2" placeholder="可选" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="variableDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingNewVariable" @click="saveNewVariable">创建</el-button>
    </template>
  </el-dialog>

  <SuiteCasePicker v-model="addDialogVisible" :groups="groupedCases" :all-cases-count="allCases.length"
    :keyword="addKeyword" :selected-ids="selectedIds" :loading="loadingAddCases" :adding="addingCases"
    :case-status-meta="caseStatusMeta" :group-selection-state="groupSelectionState"
    :is-group-collapsed="isCaseGroupCollapsed" @update:keyword="addKeyword = $event" @toggle="toggleSelect"
    @toggle-group="toggleGroupSelection" @collapse="toggleCaseGroup" @add="addSelectedCases" />

  <CaseVariableEditor
    v-model="variableEditor.visible"
    title="编排项变量覆盖"
    :subtitle="variableEditor.membership ? `仅作用于当前套件中的「${variableEditor.membership.case_name}」这一次编排` : ''"
    :variables="variableEditor.variables"
    :loading="variableEditor.loading"
    :saving="variableEditor.saving"
    :readonly="!canWriteAssets"
    :context="variableEditor.membership ? { name: variableEditor.membership.case_name, order: suiteCases.findIndex((item) => item.id === variableEditor.membership?.id) + 1 } : undefined"
    @save="saveVariableOverrides"
  />
</template>

<style scoped>
/* 左区内右侧：套件列表，吃掉左区剩余宽度并吸顶 */
.suite-sidebar {
  position: sticky;
  top: 16px;
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
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

/*
 * 页面级三栏：左区（模块树 + 套件列表）│ 套件详情。
 * 与用例页同构 —— 用例页同样是「ModuleTree + 主区」的并列结构。
 * 这里刻意不设 align-items：默认 stretch 让 .suite-left 撑满整行高度，
 * 左区内的两列才能 position: sticky 生效；改成 flex-start 会让吸顶失效。
 */
.suite-layout {
  display: flex;
  gap: 16px;
  min-height: calc(100vh - 120px);
}

/*
 * 左区固定占 42%（内含 16px 栏间距），其余宽度全部留给右侧的用例编排。
 * 注意：42% 是有意放宽的取值，已超出最初「左区最多 1/3」的要求，
 * 契约测试 suite-three-column-layout.test.ts 按 42% 断言，改这里要同步改测试。
 * 内部并排两列，视觉上仍是三栏。
 */
.suite-left {
  flex: 0 0 calc(42% - 16px);
  display: flex;
  gap: 12px;
  align-items: flex-start;
  min-width: 0;
}

/* 模块树：占左区一半的导航列（复用组件自带的卡片外观，仅补吸顶） */
.suite-tree {
  flex: 50%;
  position: sticky;
  top: 16px;
}

/* 详情：吃掉右区全部宽度 */
.suite-main {
  flex: 1;
  min-width: 0;
  min-height: calc(100vh - 120px);
}

/*
 * 【当前停用】<1800px 时左区只剩约 500px，并排的树 + 列表会把套件卡片底行
 * （用例数 + 时间 + 运行按钮）挤到换行。启用后左区自身改为上下结构：
 * 树在上（限高 30vh、自带滚动），列表在下，左区占比与详情宽度都不受影响。
 * 需要恢复时取消下面整段注释，并同步改掉契约测试里「窄屏回退目前停用」那条。
 */
/* @media (max-width: 1799px) {
  .suite-left {
    flex-direction: column;
    gap: 16px;
    align-items: stretch;
  } */

/* 竖排时 flex-basis 变成了高度，必须重置回内容高度，宽度改为撑满左区 */
/* .suite-left .suite-tree {
    flex: 0 0 auto;
    width: 100%;
    max-height: 30vh;
  }

  .suite-sidebar {
    flex: 0 0 auto;
    max-height: 50vh;
  } */
/* } */

.list-truncated {
  padding: 8px 12px;
  font-size: 12px;
  text-align: center;
}

.suite-module-select {
  width: 100%;
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
  transition: border-color .15s, background .15s, box-shadow .15s;
}

.suite-item:hover {
  border-color: var(--primary);
  box-shadow: 0 2px 8px rgba(79, 70, 229, .08);
}

.suite-item.active {
  border-color: var(--primary);
  background: var(--primary-light);
}

.suite-item-top,
.suite-item-foot {
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
  overflow: hidden;
  text-overflow: ellipsis;
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

.case-orchestration-head {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--card-bg);
  border-radius: var(--radius-card) var(--radius-card) 0 0;
  border-bottom: 1px solid var(--border);
  box-shadow: 0 2px 8px rgba(15, 23, 42, .08);
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

.accent-violet {
  background: #8b5cf6;
}

.section-head-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .case-orchestration-head {
    flex-wrap: wrap;
    gap: 8px;
  }

  .case-orchestration-head .section-head-right {
    margin-left: auto;
  }
}

.section-body {
  padding: 16px 20px;
}

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

.case-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.case-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  transition: border-color .15s, box-shadow .15s, background .15s;
}

.case-card:hover {
  border-color: var(--primary);
  background: var(--primary-light);
  box-shadow: 0 2px 8px rgba(79, 70, 229, .08);
}

.case-ghost {
  opacity: .4;
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
  min-width: 22px;
  width: auto;
  height: 22px;
  padding: 0 6px;
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

.case-order.editable {
  cursor: pointer;
}

.case-order-input {
  width: 68px;
  flex-shrink: 0;
}

.case-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}

.case-module,
.case-remove {
  flex-shrink: 0;
}

.case-variables {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 60%;
}

.case-empty {
  padding: 4px 0;
}

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

.detail-loading {
  min-height: 220px;
}

.detail-empty {
  padding-top: 80px;
}
</style>
