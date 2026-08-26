<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Draggable from 'vuedraggable'

import {
  addSuiteCase,
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
import { listCases, normalizeStep, type Step } from '@/api/cases'
import CaseStepEditor from '@/components/CaseStepEditor.vue'
import RunButton from '@/components/RunButton.vue'
import { useUnsavedChanges } from '@/composables/useUnsavedChanges'

const route = useRoute()
const projectId = Number(route.params.projectId)

const suites = ref<Suite[]>([])
const activeSuite = ref<number | null>(null)
const suiteCases = ref<SuiteCase[]>([])

// 套件变量（V2 §5.7）
const suiteVars = ref<Variable[]>([])
const varEditing = ref<{ id: number; value: string } | null>(null)

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ name: '', description: '' })

// 套件前后置步骤（方案 §2）：与用例步同构，复用 CaseStepEditor
const suiteDetail = ref<Suite | null>(null)
const setupSteps = ref<Step[]>([])
const teardownSteps = ref<Step[]>([])
const savingSteps = ref(false)
const { markDirty, markSaved } = useUnsavedChanges()
let stepsLoaded = false

function onSetupStepsChange(steps: Step[]) {
  setupSteps.value = steps
  if (stepsLoaded) markDirty()
}

function onTeardownStepsChange(steps: Step[]) {
  teardownSteps.value = steps
  if (stepsLoaded) markDirty()
}

const addDialogVisible = ref(false)
const allCases = ref<{ id: number; name: string }[]>([])
const selectedCaseId = ref<number | null>(null)
const caseKeyword = ref('')

async function loadSuites() {
  suites.value = await listSuites(projectId)
  if (!activeSuite.value && suites.value.length > 0) {
    await selectSuite(suites.value[0].id)
  }
}

async function selectSuite(id: number) {
  activeSuite.value = id
  const detail = await getSuite(id)
  suiteDetail.value = detail
  setupSteps.value = (detail.setup_steps ?? []).map((s) => normalizeStep({ ...s, phase: 'setup' }))
  teardownSteps.value = (detail.teardown_steps ?? []).map((s) => normalizeStep({ ...s, phase: 'teardown' }))
  suiteCases.value = await listSuiteCases(id)
  suiteVars.value = await listVariables({ scope: 'suite', suite_id: id })
  varEditing.value = null
  stepsLoaded = true
}

async function saveVarValue(v: Variable) {
  if (!varEditing.value) return
  await updateVariable(v.id, { value: varEditing.value.value })
  varEditing.value = null
  await selectSuite(activeSuite.value!)
  ElMessage.success('变量已更新')
}

async function removeVar(v: Variable) {
  await ElMessageBox.confirm(`确认删除变量「${v.name}」？`, '提示', { type: 'warning' })
  await deleteVariable(v.id)
  await selectSuite(activeSuite.value!)
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

async function save() {
  if (!form.value.name) {
    ElMessage.warning('请输入套件名称')
    return
  }
  if (editingId.value) {
    const updated = await updateSuite(editingId.value, form.value)
    suiteDetail.value = { ...suiteDetail.value, ...updated }
  } else {
    const suite = await createSuite(projectId, form.value)
    activeSuite.value = suite.id
    await selectSuite(suite.id)
  }
  dialogVisible.value = false
  await loadSuites()
}

async function saveSuiteSteps() {
  if (!activeSuite.value || !suiteDetail.value) return
  savingSteps.value = true
  try {
    await updateSuite(activeSuite.value, {
      name: suiteDetail.value.name,
      description: suiteDetail.value.description ?? null,
      setup_steps: setupSteps.value,
      teardown_steps: teardownSteps.value,
    })
    markSaved()
    ElMessage.success('套件配置已保存')
    await loadSuites()
  } finally {
    savingSteps.value = false
  }
}

async function remove(suite: Suite) {
  await ElMessageBox.confirm(`确认删除套件「${suite.name}」？`, '提示', { type: 'warning' })
  await deleteSuite(suite.id)
  if (activeSuite.value === suite.id) activeSuite.value = null
  await loadSuites()
}

async function openAddCase() {
  caseKeyword.value = ''
  selectedCaseId.value = null
  allCases.value = []
  const data = await listCases(projectId, { page: 1, page_size: 200 })
  const inSuite = new Set(suiteCases.value.map((c) => c.case_id))
  allCases.value = data.items.filter((c) => !inSuite.has(c.id))
  addDialogVisible.value = true
}

async function addCase() {
  if (!selectedCaseId.value) {
    ElMessage.warning('请选择用例')
    return
  }
  await addSuiteCase(activeSuite.value!, selectedCaseId.value)
  addDialogVisible.value = false
  await selectSuite(activeSuite.value!)
}

async function removeCase(suiteCase: SuiteCase) {
  await removeSuiteCase(activeSuite.value!, suiteCase.case_id)
  await selectSuite(activeSuite.value!)
}

async function onReorder() {
  if (!activeSuite.value) return
  await reorderSuiteCases(activeSuite.value, suiteCases.value.map((c) => c.case_id))
  await selectSuite(activeSuite.value)
}

onMounted(loadSuites)
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="9">
      <div class="suite-list">
        <div class="toolbar-card">
          <el-button type="primary" @click="openCreate">新建套件</el-button>
        </div>
        <div
          v-for="s in suites"
          :key="s.id"
          class="suite-item"
          :class="{ active: s.id === activeSuite }"
          @click="selectSuite(s.id)"
        >
          <div class="suite-name">{{ s.name }}</div>
          <div class="suite-meta">
            <el-tag size="small" type="info">{{ s.case_count }} 个用例</el-tag>
            <span class="suite-actions">
              <RunButton :type="'suite'" :id="s.id" :name="s.name" />
              <el-button size="small" text @click.stop="openEdit(s)">编辑</el-button>
              <el-button size="small" type="danger" text @click.stop="remove(s)">删除</el-button>
            </span>
          </div>
        </div>
        <el-empty v-if="suites.length === 0" description="暂无套件" />
      </div>
    </el-col>

    <el-col :span="15">
      <template v-if="activeSuite">
        <div class="toolbar-card">
          <el-button type="primary" @click="openAddCase">添加用例</el-button>
          <span class="spacer"></span>
        </div>
        <Draggable v-model="suiteCases" item-key="id" handle=".drag-handle" @end="onReorder">
          <template #item="{ element }">
            <el-card class="case-item" shadow="never">
              <div class="case-row">
                <span class="drag-handle">⠿</span>
                <span class="case-name">{{ element.case_name }}</span>
                <el-tag v-if="element.module_name" size="small">{{ element.module_name }}</el-tag>
                <el-button size="small" type="danger" text @click="removeCase(element)">移除</el-button>
              </div>
            </el-card>
          </template>
        </Draggable>
        <el-empty v-if="suiteCases.length === 0" description="该套件暂无用例，点击「添加用例」" />

        <!-- 套件前后置步骤（方案 §2）：与用例步同构，复用 CaseStepEditor -->
        <el-collapse class="suite-steps-collapse">
          <el-collapse-item name="setup">
            <template #title>
              <span>套件前置步骤</span>
              <el-tag size="small" type="warning" class="step-count">{{ setupSteps.length }}</el-tag>
            </template>
            <CaseStepEditor
              :model-value="setupSteps"
              @update:model-value="onSetupStepsChange"
              phase="setup"
              title="前置操作"
              description="运行时在套件内每个用例主体之前执行"
              tone="warning"
            />
          </el-collapse-item>
          <el-collapse-item name="teardown">
            <template #title>
              <span>套件后置步骤</span>
              <el-tag size="small" type="success" class="step-count">{{ teardownSteps.length }}</el-tag>
            </template>
            <CaseStepEditor
              :model-value="teardownSteps"
              @update:model-value="onTeardownStepsChange"
              phase="teardown"
              title="后置操作"
              description="运行时在套件内每个用例完成后执行；主体验证失败时仍会尝试清理"
              tone="success"
            />
          </el-collapse-item>
        </el-collapse>
        <div class="save-steps">
          <el-button type="primary" :loading="savingSteps" @click="saveSuiteSteps">保存套件配置</el-button>
        </div>

        <!-- 套件变量（V2 §5.7） -->
        <el-collapse class="suite-vars">
          <el-collapse-item title="套件变量" name="vars">
            <div v-for="v in suiteVars" :key="v.id" class="var-row">
              <span class="var-name">{{ v.name }}</span>
              <el-input
                v-if="varEditing?.id === v.id"
                v-model="varEditing.value"
                size="small"
                class="var-input"
                @keyup.enter="saveVarValue(v)"
              />
              <span v-else class="var-value">{{ v.value || '—' }}</span>
              <el-button v-if="varEditing?.id === v.id" size="small" type="primary" text @click="saveVarValue(v)">保存</el-button>
              <el-button v-else size="small" text @click="varEditing = { id: v.id, value: v.value }">编辑</el-button>
              <el-button size="small" type="danger" text @click="removeVar(v)">删除</el-button>
            </div>
            <el-empty v-if="suiteVars.length === 0" description="暂无套件变量" :image-size="40" />
          </el-collapse-item>
        </el-collapse>
      </template>
      <el-empty v-else description="请选择左侧套件" />
    </el-col>
  </el-row>

  <el-dialog v-model="dialogVisible" :title="editingId ? '编辑套件' : '新建套件'" width="480px">
    <el-form label-width="80px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="form.description" type="textarea" :rows="3" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" @click="save">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="addDialogVisible" title="添加用例" width="520px">
    <el-input v-model="caseKeyword" placeholder="搜索用例名" clearable />
    <el-select v-model="selectedCaseId" filterable placeholder="选择用例" class="full case-select">
      <el-option
        v-for="c in allCases.filter((c) => !caseKeyword || c.name.includes(caseKeyword))"
        :key="c.id"
        :label="c.name"
        :value="c.id"
      />
    </el-select>
    <template #footer>
      <el-button @click="addDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="addCase">添加</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.suite-list {
  border-right: 1px solid var(--border);
  padding-right: 12px;
}
.suite-item {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.suite-item.active {
  border-color: var(--primary);
  background: var(--primary-light);
}
.suite-name {
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--text);
}
.suite-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.case-item {
  margin-bottom: 8px;
  border-radius: 8px;
  border: 1px solid var(--border);
}
.case-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.drag-handle {
  cursor: move;
  color: #999;
}
.drag-handle:hover {
  color: var(--primary);
}
.case-name {
  flex: 1;
}
.full {
  width: 100%;
}
.case-select {
  margin-top: 12px;
}
.suite-vars {
  margin-top: 16px;
}
.suite-steps-collapse {
  margin-top: 16px;
}
.step-count {
  margin-left: 8px;
}
.save-steps {
  margin-top: 8px;
  text-align: right;
}
.var-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
}
.var-name {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  min-width: 120px;
  color: var(--text);
}
.var-value {
  color: var(--text-2);
  min-width: 160px;
}
.var-input {
  width: 180px;
}
</style>