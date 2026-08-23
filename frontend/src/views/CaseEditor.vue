<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Draggable from 'vuedraggable'

import {
  ACTIONS,
  ASSERTION_TYPES,
  CASE_STATUS,
  actionMeta,
  assertionMeta,
  createCase,
  defaultParams,
  getCase,
  normalizeStep,
  updateCase,
  type Assertion,
  type Step,
  type TestCase,
} from '@/api/cases'
import { listModules } from '@/api/elements'
import ElementSelector from '@/components/ElementSelector.vue'
import { useUnsavedChanges } from '@/composables/useUnsavedChanges'

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

function stepMeta(step: Step) {
  return actionMeta(step.action)
}

function assertMeta(assertion: Assertion) {
  return assertionMeta(assertion.type)
}

function addStep() {
  const steps = form.steps as Step[]
  steps.push({
    order: steps.length + 1,
    action: 'click',
    params: defaultParams(actionMeta('click').fields),
    element_id: null,
    description: '',
    continue_on_failure: false,
  })
}

function onStepActionChange(step: Step) {
  // CR-09：切换动作后按元数据重建参数
  step.params = defaultParams(actionMeta(step.action).fields)
}

function removeStep(index: number) {
  ;(form.steps as Step[]).splice(index, 1)
  reorderSteps()
}

function reorderSteps() {
  ;(form.steps as Step[]).forEach((s, i) => (s.order = i + 1))
}

function addAssertion() {
  const assertions = form.assertions as Assertion[]
  assertions.push({
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
      assertions: form.assertions as Assertion[],
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
    form.assertions = data.assertions.map((a) => ({ ...a, params: a.params ?? {} }))
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

    <div class="content-card mb16">
      <div class="section-title-row">
        <span class="section-title">执行步骤</span>
        <el-button type="primary" size="small" @click="addStep">添加步骤</el-button>
      </div>
      <Draggable v-model="form.steps" item-key="order" handle=".drag-handle" class="step-list" @end="reorderSteps">
        <template #item="{ element, index }">
          <el-card class="step-card step" shadow="never">
            <div class="step-head">
              <span class="drag-handle">⠿</span>
              <span class="step-badge step">{{ index + 1 }}</span>
              <el-select
                v-model="element.action"
                class="action-select"
                @change="onStepActionChange(element)"
              >
                <el-option v-for="a in ACTIONS" :key="a.value" :label="a.label" :value="a.value" />
              </el-select>
              <span class="continue-label">失败后继续</span>
              <el-switch v-model="element.continue_on_failure" size="small" />
              <el-button type="danger" text size="small" @click="removeStep(index)">删除</el-button>
            </div>
            <div class="step-body">
              <div v-if="stepMeta(element).needsElement" class="step-row">
                <span class="field-label">元素</span>
                <ElementSelector v-model="element.element_id" />
              </div>
              <div v-for="f in stepMeta(element).fields" :key="f.key" class="step-row">
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
                  :min="f.min"
                  :max="f.max"
                  :placeholder="f.placeholder"
                  class="w-200"
                />
              </div>
              <div class="step-row">
                <span class="field-label">描述</span>
                <el-input v-model="element.description" placeholder="步骤说明（可选）" />
              </div>
            </div>
          </el-card>
        </template>
      </Draggable>
      <div class="add-more">
        <el-button type="primary" plain class="w-full" @click="addStep">+ 添加步骤</el-button>
      </div>
    </div>

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
</template>

<style scoped>
.mb16 {
  margin-bottom: 16px;
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
