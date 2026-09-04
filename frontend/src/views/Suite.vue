<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MoreFilled, Plus, Search } from '@element-plus/icons-vue'
import Draggable from 'vuedraggable'

import { createSuite, createVariable, deleteSuite, updateSuite, type Suite, type SuiteCase } from '@/api/suites'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import RunButton from '@/components/RunButton.vue'
import SuiteCasePicker from '@/components/SuiteCasePicker.vue'
import SuiteStepSection from '@/components/SuiteStepSection.vue'
import { useSuiteCases } from '@/composables/useSuiteCases'
import { useSuiteDetail } from '@/composables/useSuiteDetail'
import { useSuiteList } from '@/composables/useSuiteList'
import { usePermission } from '@/composables/usePermission'
import { useProjectContextStore } from '@/stores/projectContext'
import { formatDateTime } from '@/utils/format'
import { parseSuiteId } from '@/utils/suiteNavigation'

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
  filteredSuites,
  suiteStatusMeta,
  selectSuite,
  loadSuites,
  clearActiveSuite,
} = suiteList
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
} = suiteCaseState

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ name: '', description: '' })
const savingSuite = ref(false)
const variableDialogVisible = ref(false)
const variableForm = ref({ name: '', value: '', description: '' })
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

function openCreateVariable() {
  variableForm.value = { name: '', value: '', description: '' }
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

function onSuiteMenu(cmd: string | number | object) {
  if (!suiteDetail.value) return
  const key = String(cmd)
  if (key === 'edit') openEdit(suiteDetail.value)
  else if (key === 'delete') void remove(suiteDetail.value)
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
  <el-row :gutter="16" class="suite-page">
    <el-col :xs="24" :lg="9" class="suite-col">
      <aside class="suite-sidebar">
        <div class="sidebar-head">
          <div class="sidebar-title-wrap">
            <h2 class="v2-card-title">套件管理</h2>
            <span class="sidebar-count v2-aux">{{ suites.length }} 个</span>
          </div>
          <el-button v-if="canWriteAssets" type="primary" :icon="Plus" @click="openCreate">新建套件</el-button>
        </div>
        <div class="sidebar-filter">
          <el-input v-model="keyword" placeholder="按名称或描述搜索" clearable class="sidebar-search">
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
            <EmptyState v-if="suites.length === 0" title="还没创建套件" description="套件用于批量编排用例，并可配置前后置步骤与变量。"
              :action-label="canWriteAssets ? '新建套件' : undefined" @action="openCreate" />
            <div v-else class="no-match v2-aux">没有找到与「{{ keyword }}」匹配的套件</div>
          </template>
        </div>
      </aside>
    </el-col>

    <el-col :xs="24" :lg="15" class="suite-col">
      <template v-if="activeSuite && suiteDetail">
        <div v-loading="loadingDetail" class="detail-stack">
          <PageHeader :title="suiteDetail.name" :description="suiteDetail.description ?? ''">
            <RunButton :type="'suite'" :id="suiteDetail.id" :name="suiteDetail.name" />
            <el-dropdown v-if="canWriteAssets" trigger="click" @command="onSuiteMenu">
              <el-button :icon="MoreFilled">套件操作</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit">编辑信息</el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除套件</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </PageHeader>

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
            <header class="section-head">
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
              <Draggable v-model="suiteCases" :disabled="!canWriteAssets" item-key="id" handle=".drag-handle" ghost-class="case-ghost"
                class="case-list" @end="onReorder">
                <template #item="{ element, index }">
                  <div class="case-card" @dblclick="openCaseEditor(element)">
                    <div class="case-row"><span class="drag-handle" title="拖拽排序">⠿</span><span class="case-order">{{
                        index + 1 }}</span><span class="case-name" :title="element.case_name">{{ element.case_name
                        }}</span><el-tag v-if="element.module_name" size="small" type="info" effect="plain"
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
            :readonly="!canWriteAssets"
            :dirty="dirty" :saving="savingSteps" @update:setup-steps="onSetupStepsChange"
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
                    class="var-input" @keyup.enter="saveVarValue(variable, activeSuite)"
                    @blur="saveVarValue(variable, activeSuite)" />
                  <span v-else class="var-value" :title="variable.value">{{ variable.value || '—' }}</span>
                  <span class="var-actions">
                    <el-button v-if="varEditing?.id === variable.id" size="small" type="primary" text
                      :loading="savingVar" @click="saveVarValue(variable, activeSuite)">保存</el-button>
                    <el-button v-if="canWriteAssets && varEditing?.id !== variable.id" size="small" text @click="startEditVar(variable)">编辑</el-button>
                    <el-button v-if="canWriteAssets" size="small" type="danger" text @click="removeVar(variable, activeSuite)">删除</el-button>
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
    </el-col>
  </el-row>

  <el-dialog v-model="dialogVisible" :title="editingId ? '编辑套件' : '新建套件'" width="480px" append-to-body>
    <el-form label-width="80px" @submit.prevent="save">
      <el-form-item label="名称" required><el-input v-model="form.name" placeholder="请输入套件名称" maxlength="255"
          @keyup.enter="save" /></el-form-item>
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
        <el-input v-model="variableForm.value" placeholder="请输入变量值" />
      </el-form-item>
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
</template>

<style scoped>
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

.case-module,
.case-remove {
  flex-shrink: 0;
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
