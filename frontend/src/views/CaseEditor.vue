<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Draggable from 'vuedraggable'

import {
  ASSERTION_TYPES,
  CASE_STATUS,
  assertionMeta,
  createCase,
  defaultParams,
  getCase,
  normalizeAssertion,
  normalizeStep,
  updateCase,
  type Assertion,
  type Step,
  type StepPhase,
  type TestCase,
} from '@/api/cases'
import { listModules } from '@/api/elements'
import CaseStepEditor from '@/components/CaseStepEditor.vue'
import ElementSelector from '@/components/ElementSelector.vue'
import { useUnsavedChanges } from '@/composables/useUnsavedChanges'
import { buildCaseEditorSummary, caseEditorSummaryText } from '@/utils/caseEditorSummary'

const route = useRoute()
const router = useRouter()
const projectId = Number(route.params.projectId)
const caseId = computed<number | null>(() => {
  if (route.name === 'CaseNew') return null
  const p = route.params.caseId
  if (p == null || p === 'new') return null
  const n = Number(p)
  return Number.isFinite(n) ? n : null
})

const loading = ref(false)
const modules = ref<{ id: number; name: string }[]>([])

const form = reactive<Partial<TestCase>>({
  name: '',
  module_id: null,
  description: '',
  status: 'draft',
  steps: [] as Step[],
  assertions: [] as Assertion[],
  variables: {} as Record<string, unknown>,
})

const variableEntries = ref<{ key: string; value: string }[]>([])
const isEdit = computed(() => caseId.value !== null)

// V2 §4.2：编辑页 dirty 离开确认
const { markDirty, markSaved } = useUnsavedChanges()
watch(
  () => JSON.stringify({ ...form, variables: collectVariables() }),
  () => {
    if (loadedOnce.value) markDirty()
  },
  { deep: true },
)
const loadedOnce = ref(false)

function assertMeta(assertion: Assertion) {
  return assertionMeta(assertion.type)
}

function phaseSteps(phase: StepPhase) {
  return computed<Step[]>({
    get: () => (form.steps as Step[]).filter((step) => (step.phase ?? 'main') === phase),
    set: (steps) => {
      const other = (form.steps as Step[]).filter((step) => (step.phase ?? 'main') !== phase)
      // 保留步骤对象身份，使 CaseStepEditor 的纯 UI 折叠状态在编辑/拖拽后仍对应原步骤。
      const normalized = steps.map((step, index) => {
        step.phase = phase
        step.order = index + 1
        return step
      })
      form.steps = [...other, ...normalized]
    },
  })
}

const setupSteps = phaseSteps('setup')
const mainSteps = phaseSteps('main')
const teardownSteps = phaseSteps('teardown')

// 收起/展开：编辑完成后可折叠为一行简略信息，点击展开
const collapsed = ref(false)

const moduleName = computed(() => {
  if (form.module_id == null) return '未分组'
  return modules.value.find((m) => m.id === form.module_id)?.name ?? '未分组'
})

const summaryMeta = computed(() => {
  return buildCaseEditorSummary(
    (form.steps as Step[]) ?? [],
    (form.assertions as Assertion[]) ?? [],
    variableEntries.value,
  )
})

const summaryText = computed(() => caseEditorSummaryText(moduleName.value, summaryMeta.value))

function statusLabel(s: string | undefined) {
  return CASE_STATUS.find((x) => x.value === s)?.label ?? s
}

function statusType(s: string | undefined): 'info' | 'success' | 'danger' {
  if (s === 'active') return 'success'
  if (s === 'disabled') return 'danger'
  return 'info'
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
}

function addAssertion() {
  const assertions = form.assertions as Assertion[]
  assertions.push({
    key: crypto.randomUUID(),
    order: assertions.length + 1,
    type: 'element_exists',
    params: defaultParams(assertionMeta('element_exists').fields),
    element_id: null,
    description: '',
  })
}

function onAssertionTypeChange(assertion: Assertion) {
  assertion.params = defaultParams(assertionMeta(assertion.type).fields)
}

function removeAssertion(index: number) {
  ;(form.assertions as Assertion[]).splice(index, 1)
  reorderAssertions()
}

function reorderAssertions() {
  ;(form.assertions as Assertion[]).forEach((a, i) => (a.order = i + 1))
}

function addVariable() {
  variableEntries.value.push({ key: '', value: '' })
}

function removeVariable(index: number) {
  variableEntries.value.splice(index, 1)
}

function collectVariables(): Record<string, unknown> {
  const vars: Record<string, unknown> = {}
  for (const entry of variableEntries.value) {
    if (entry.key) vars[entry.key] = entry.value
  }
  return vars
}

async function save() {
  if (!form.name) {
    ElMessage.warning('请输入用例名称')
    return
  }
  loading.value = true
  try {
    const payload: Partial<TestCase> = {
      name: form.name,
      module_id: form.module_id,
      description: form.description,
      status: form.status,
      steps: (form.steps as Step[]).map(normalizeStep),
      assertions: (form.assertions as Assertion[]).map(normalizeAssertion),
      variables: collectVariables(),
    }
    if (isEdit.value) {
      await updateCase(caseId.value!, payload)
      ElMessage.success('已保存')
      markSaved()
      router.push(`/projects/${projectId}/cases`)
    } else {
      await createCase(projectId, payload)
      ElMessage.success('已创建')
      markSaved()
      router.push(`/projects/${projectId}/cases`)
    }
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  modules.value = await listModules(projectId)
  if (isEdit.value) {
    const data = await getCase(caseId.value!)
    form.name = data.name
    form.module_id = data.module_id
    form.description = data.description ?? ''
    form.status = data.status
    // Step 4：加载旧数据时归一化 continue_on_failure，且清理历史留在 params 里的字段
    form.steps = data.steps.map(normalizeStep)
    form.assertions = data.assertions.map(normalizeAssertion)
    form.variables = data.variables
    variableEntries.value = Object.entries(data.variables).map(([key, value]) => ({
      key,
      value: String(value),
    }))
  }
  loadedOnce.value = true
})
</script>

<template>
  <div v-loading="loading">
    <!-- 收起/展开摘要条：折叠后仅显示一行用例简略信息 -->
    <div
      class="editor-summary"
      :class="{ collapsed }"
      role="button"
      tabindex="0"
      :aria-expanded="!collapsed"
      aria-controls="case-editor-body"
      @click="toggleCollapsed"
      @keydown.enter.prevent="toggleCollapsed"
      @keydown.space.prevent="toggleCollapsed"
    >
      <span class="sum-icon">{{ collapsed ? '▸' : '▾' }}</span>
      <span class="sum-name">{{ form.name || '未命名用例' }}</span>
      <el-tag :type="statusType(form.status)" size="small">{{ statusLabel(form.status) }}</el-tag>
      <span class="sum-meta" :title="summaryText">{{ summaryText }}</span>
      <span class="sum-hint">{{ collapsed ? '点击展开' : '点击收起' }}</span>
    </div>

    <div id="case-editor-body" v-show="!collapsed" class="editor-body">
      <div class="content-card mb16">
        <div class="section-title">基本信息</div>
        <el-form label-width="80px" class="basic-form">
          <el-form-item label="名称" required>
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="模块">
            <el-select v-model="form.module_id" clearable placeholder="选择模块" class="w-200">
              <el-option v-for="m in modules" :key="m.id" :label="m.name" :value="m.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-radio-group v-model="form.status">
              <el-radio v-for="s in CASE_STATUS" :key="s.value" :value="s.value">{{ s.label }}</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="form.description" type="textarea" :rows="2" />
          </el-form-item>
        </el-form>
      </div>

    <CaseStepEditor
      v-model="setupSteps"
      phase="setup"
      title="前置操作"
      description="运行时勾选后，在每个用例主体步骤之前执行"
      tone="warning"
    />

    <CaseStepEditor
      v-model="mainSteps"
      phase="main"
      title="执行步骤"
      description="用例的主体操作，始终执行"
      tone="primary"
    />

    <CaseStepEditor
      v-model="teardownSteps"
      phase="teardown"
      title="后置操作"
      description="运行时勾选后，在断言完成后执行；主体失败时仍会尝试清理"
      tone="success"
    />

    <div class="content-card mb16">
      <div class="section-title-row">
        <span class="section-title">断言</span>
        <el-button type="primary" size="small" @click="addAssertion">添加断言</el-button>
      </div>
      <Draggable v-model="form.assertions" item-key="order" handle=".drag-handle" class="step-list" @end="reorderAssertions">
        <template #item="{ element, index }">
          <el-card class="step-card assertion" shadow="never">
            <div class="step-head">
              <span class="drag-handle">⠿</span>
              <span class="step-badge assertion">{{ index + 1 }}</span>
              <el-select
                v-model="element.type"
                class="action-select"
                @change="onAssertionTypeChange(element)"
              >
                <el-option v-for="a in ASSERTION_TYPES" :key="a.value" :label="a.label" :value="a.value" />
              </el-select>
              <el-button type="danger" text size="small" @click="removeAssertion(index)">删除</el-button>
            </div>
            <div class="step-body">
              <div v-if="assertMeta(element).needsElement" class="step-row">
                <span class="field-label">元素</span>
                <ElementSelector v-model="element.element_id" />
              </div>
              <div v-for="f in assertMeta(element).fields" :key="f.key" class="step-row">
                <span class="field-label">{{ f.label }}</span>
                <el-select
                  v-if="f.type === 'select'"
                  v-model="element.params![f.key]"
                  class="w-200"
                >
                  <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-switch v-else-if="f.type === 'switch'" v-model="element.params![f.key]" />
                <el-input
                  v-else
                  v-model="element.params![f.key]"
                  :type="f.type === 'number' ? 'number' : 'text'"
                  :placeholder="f.placeholder"
                  class="w-200"
                />
              </div>
              <div class="step-row">
                <span class="field-label">描述</span>
                <el-input v-model="element.description" placeholder="断言说明（可选）" />
              </div>
            </div>
          </el-card>
        </template>
      </Draggable>
      <div class="add-more">
        <el-button type="primary" plain class="w-full" @click="addAssertion">+ 添加断言</el-button>
      </div>
    </div>

    <div class="content-card mb16">
      <div class="section-title-row">
        <span class="section-title">用例变量</span>
        <el-button type="primary" size="small" @click="addVariable">添加变量</el-button>
      </div>
      <div v-for="(entry, idx) in variableEntries" :key="idx" class="variable-row">
        <el-input v-model="entry.key" placeholder="变量名" class="var-name" />
        <el-input v-model="entry.value" placeholder="变量值" class="var-value" />
        <el-button type="danger" text @click="removeVariable(idx)">删除</el-button>
      </div>
      <div class="add-more">
        <el-button type="primary" plain class="w-full" @click="addVariable">+ 添加变量</el-button>
      </div>
    </div>

    <div class="footer">
      <el-button @click="router.push(`/projects/${projectId}/cases`)">返回</el-button>
      <el-button type="primary" @click="save">保存</el-button>
    </div>
    </div>
  </div>
</template>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}
.editor-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 16px;
  margin-bottom: 16px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.editor-summary:hover {
  border-color: var(--primary);
  box-shadow: 0 2px 8px rgba(79, 70, 229, 0.1);
}
.editor-summary:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.editor-summary.collapsed {
  margin-bottom: 0;
}
.sum-icon {
  color: var(--primary);
  font-size: 14px;
  flex-shrink: 0;
}
.sum-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}
.sum-meta {
  color: var(--text-2);
  font-size: 13px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sum-hint {
  margin-left: auto;
  color: var(--primary);
  font-size: 12px;
  flex-shrink: 0;
}
.editor-body {
  display: flex;
  flex-direction: column;
}
@media (max-width: 768px) {
  .editor-summary {
    gap: 8px;
    padding: 10px 12px;
  }
  .sum-name {
    max-width: 34%;
  }
  .sum-hint {
    display: none;
  }
}
.content-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 14px;
}
.section-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.section-title-row .section-title {
  margin-bottom: 0;
}
.basic-form {
  max-width: 720px;
}
.w-200 {
  width: 200px;
}
.step-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.step-card {
  margin-bottom: 0;
  border-radius: 8px;
}
.step-card.step {
  border-left: 3px solid var(--primary);
}
.step-card.assertion {
  border-left: 3px solid var(--success);
}
.step-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.drag-handle {
  cursor: move;
  color: #999;
}
.drag-handle:hover {
  color: var(--primary);
}
.step-badge {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.step-badge.step {
  background: var(--primary-light);
  color: var(--primary);
}
.step-badge.assertion {
  background: rgba(16, 185, 129, 0.12);
  color: var(--success);
}
.action-select {
  flex: 1;
  max-width: 220px;
}
.continue-label {
  color: #888;
  font-size: 13px;
  flex-shrink: 0;
}
.step-body {
  margin-top: 8px;
}
.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.field-label {
  width: 80px;
  color: #888;
  flex-shrink: 0;
}
.variable-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  max-width: 560px;
}
.var-name {
  width: 180px;
}
.var-value {
  flex: 1;
}
.add-more {
  margin-top: 10px;
}
.add-more .w-full {
  width: 100%;
  border-style: dashed;
}
.footer {
  position: sticky;
  bottom: 0;
  background: #fff;
  border-top: 1px solid var(--border);
  padding: 14px 0;
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  z-index: 10;
}
</style>
