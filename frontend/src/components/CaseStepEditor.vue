<script setup lang="ts">
import { ref, toRaw, watch } from 'vue'

import Draggable from 'vuedraggable'

import {
  ACTIONS,
  ASSERTION_TYPES,
  actionMeta,
  assertionMeta,
  defaultParams,
  type Assertion,
  type Step,
  type StepPhase,
} from '@/api/cases'
import ElementSelector from '@/components/ElementSelector.vue'
import { stepActionLabel, stepSummaryText, type ElementNameMap } from '@/utils/stepEditorSummary'
import { createUuid } from '@/utils/uuid'

const props = defineProps<{
  modelValue: Step[]
  phase: StepPhase
  title: string
  description: string
  tone?: 'primary' | 'warning' | 'success'
  /** 当前项目 ID，用于限制元素选择范围并回查已选元素。 */
  projectId?: number
  /** 元素 id → 名称映射（收起摘要显示元素名而非编号；未提供时回退编号） */
  elementNames?: ElementNameMap
  /** 套件前后置步骤仅支持动作，不显示断言编辑区域。 */
  allowAssertions?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [steps: Step[]] }>()

// 步骤没有数据库行 ID；使用 WeakMap 保存纯 UI 标识，避免折叠状态进入接口 payload。
const stepUiKeyIds = new Map<string, number>()
const stepUiIds = new WeakMap<object, number>()
const collapsedStepIds = ref(new Set<number>())
let nextStepUiId = 0

// 「进入编辑页默认全部收起」：仅对首次加载的步骤生效一次；
// 之后的添加/删除/拖拽由用户显式操作，不再自动折叠。
let collapseApplied = false
watch(
  () => props.modelValue,
  (steps) => {
    if (collapseApplied || !steps.length) return
    collapseApplied = true
    collapsedStepIds.value = new Set(steps.map((s) => stepUiId(s)))
  },
)

function stepUiId(step: Step): number {
  if (step.key) {
    const existingByKey = stepUiKeyIds.get(step.key)
    if (existingByKey != null) return existingByKey
    const createdByKey = ++nextStepUiId
    stepUiKeyIds.set(step.key, createdByKey)
    return createdByKey
  }
  const rawStep = toRaw(step)
  const existing = stepUiIds.get(rawStep)
  if (existing != null) return existing
  const created = ++nextStepUiId
  stepUiIds.set(rawStep, created)
  return created
}

function isStepCollapsed(step: Step): boolean {
  return collapsedStepIds.value.has(stepUiId(step))
}

function toggleStep(step: Step) {
  const id = stepUiId(step)
  if (collapsedStepIds.value.has(id)) collapsedStepIds.value.delete(id)
  else collapsedStepIds.value.add(id)
}

function expandStep(step: Step) {
  if (isStepCollapsed(step)) toggleStep(step)
}

function onCollapsedStepKeydown(event: KeyboardEvent, step: Step) {
  if (!isStepCollapsed(step) || !['Enter', ' '].includes(event.key)) return
  event.preventDefault()
  toggleStep(step)
}

function update(steps: Step[]) {
  const normalized = steps.map((step, index) => {
    const nextStep = { ...step, phase: props.phase, order: index + 1 }
    stepUiIds.set(nextStep, stepUiId(step))
    return nextStep
  })
  emit(
    'update:modelValue',
    normalized,
  )
}

function addStep() {
  // 用户主动添加：视作已开始编辑，后续步骤不再自动折叠
  collapseApplied = true
  update([
    ...props.modelValue,
    {
      order: props.modelValue.length + 1,
      phase: props.phase,
      action: 'click',
      params: defaultParams(actionMeta('click').fields),
      element_id: null,
      description: '',
      continue_on_failure: false,
      ...(props.allowAssertions === false ? {} : { assertions: [] }),
    },
  ])
}

function removeStep(step: Step) {
  collapsedStepIds.value.delete(stepUiId(step))
  update(props.modelValue.filter((candidate) => {
    if (candidate === step) return false
    return !step.key || candidate.key !== step.key
  }))
}

function onActionChange(step: Step) {
  step.params = defaultParams(actionMeta(step.action).fields)
  update([...props.modelValue])
}

function onDragEnd() {
  update([...props.modelValue])
}

function addAssertion(step: Step) {
  const assertions = step.assertions ?? (step.assertions = [])
  assertions.push({
    key: createUuid(),
    order: assertions.length + 1,
    type: 'element_exists',
    params: defaultParams(assertionMeta('element_exists').fields),
    element_id: null,
    description: '',
  })
  update([...props.modelValue])
}

function removeAssertion(step: Step, index: number) {
  step.assertions?.splice(index, 1)
  step.assertions?.forEach((assertion, assertionIndex) => {
    assertion.order = assertionIndex + 1
  })
  update([...props.modelValue])
}

function onAssertionTypeChange(assertion: Assertion) {
  assertion.params = defaultParams(assertionMeta(assertion.type).fields)
  update([...props.modelValue])
}
</script>

<template>
  <div class="content-card mb16 phase-card" :class="`tone-${tone ?? 'primary'}`">
    <div class="section-title-row">
      <div>
        <div class="section-title">{{ title }}</div>
        <div class="section-description">{{ description }}</div>
      </div>
      <el-button type="primary" plain size="small" @click="addStep">添加操作</el-button>
    </div>
    <Draggable
      :model-value="modelValue"
      :item-key="stepUiId"
      handle=".drag-handle"
      class="step-list"
      group="case-steps"
      @update:model-value="update"
      @end="onDragEnd"
    >
      <template #item="{ element, index }">
        <el-card class="step-card" :class="{ collapsed: isStepCollapsed(element) }" shadow="never">
          <div
            class="step-head"
            :class="{ collapsed: isStepCollapsed(element) }"
            :role="isStepCollapsed(element) ? 'button' : undefined"
            :tabindex="isStepCollapsed(element) ? 0 : undefined"
            :aria-expanded="!isStepCollapsed(element)"
            @click="expandStep(element)"
            @keydown="onCollapsedStepKeydown($event, element)"
          >
            <span class="drag-handle" @click.stop>⠿</span>
            <span class="step-badge">{{ index + 1 }}</span>
            <div v-if="isStepCollapsed(element)" class="step-summary" :title="stepSummaryText(element, props.elementNames)">
              <span class="step-action-label">{{ stepActionLabel(element) }}</span>
              <span class="step-summary-text">{{ stepSummaryText(element, props.elementNames) }}</span>
            </div>
            <el-select v-else v-model="element.action" class="action-select" @change="onActionChange(element)">
              <el-option v-for="action in ACTIONS" :key="action.value" :label="action.label" :value="action.value" />
            </el-select>
            <template v-if="!isStepCollapsed(element)">
              <span class="continue-label">失败后继续</span>
              <el-switch v-model="element.continue_on_failure" size="small" @change="update([...modelValue])" />
            </template>
            <el-button text size="small" @click.stop="toggleStep(element)">
              {{ isStepCollapsed(element) ? '展开' : '收起' }}
            </el-button>
            <el-button type="danger" text size="small" @click.stop="removeStep(element)">删除</el-button>
          </div>
          <div v-show="!isStepCollapsed(element)" class="step-body">
            <div v-if="actionMeta(element.action).needsElement" class="step-row">
              <span class="field-label">{{ actionMeta(element.action).elementLabel ?? '元素' }}</span>
              <ElementSelector v-model="element.element_id" :project-id="projectId" />
            </div>
            <div v-for="field in actionMeta(element.action).fields" :key="field.key" class="step-row">
              <span class="field-label">{{ field.label }}</span>
              <el-select
                v-if="field.type === 'select'"
                v-model="element.params![field.key]"
                class="w-200"
              >
                <el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
              <el-switch v-else-if="field.type === 'switch'" v-model="element.params![field.key]" />
              <el-input
                v-else
                v-model="element.params![field.key]"
                :type="field.type === 'number' ? 'number' : 'text'"
                :min="field.min"
                :max="field.max"
                :placeholder="field.placeholder"
                class="w-200"
              />
            </div>
            <div class="step-row">
              <span class="field-label">描述</span>
              <el-input v-model="element.description" placeholder="操作说明（可选）" />
            </div>
            <div v-if="props.allowAssertions !== false" class="assertion-section">
              <div class="assertion-title-row">
                <div>
                  <strong>步骤后断言</strong>
                  <span class="assertion-tip">动作成功后立即按顺序校验</span>
                </div>
                <el-button type="primary" plain size="small" @click="addAssertion(element)">添加断言</el-button>
              </div>
              <el-card
                v-for="(assertion, assertionIndex) in element.assertions ?? []"
                :key="assertion.key ?? assertionIndex"
                class="assertion-card"
                shadow="never"
              >
                <div class="assertion-head">
                  <span class="assertion-badge">{{ assertionIndex + 1 }}</span>
                  <el-select v-model="assertion.type" class="action-select" @change="onAssertionTypeChange(assertion)">
                    <el-option v-for="item in ASSERTION_TYPES" :key="item.value" :label="item.label" :value="item.value" />
                  </el-select>
                  <el-button type="danger" text size="small" @click="removeAssertion(element, assertionIndex)">删除</el-button>
                </div>
                <div v-if="assertionMeta(assertion.type).needsElement" class="step-row">
                  <span class="field-label">元素</span>
                  <ElementSelector v-model="assertion.element_id" :project-id="projectId" />
                </div>
                <div v-for="field in assertionMeta(assertion.type).fields" :key="field.key" class="step-row">
                  <span class="field-label">{{ field.label }}</span>
                  <el-select v-if="field.type === 'select'" v-model="assertion.params![field.key]" class="w-200">
                    <el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" />
                  </el-select>
                  <el-switch v-else-if="field.type === 'switch'" v-model="assertion.params![field.key]" />
                  <el-input
                    v-else
                    v-model="assertion.params![field.key]"
                    :type="field.type === 'number' ? 'number' : 'text'"
                    :min="field.min"
                    :max="field.max"
                    :placeholder="field.placeholder"
                    class="w-200"
                  />
                </div>
                <div class="step-row">
                  <span class="field-label">描述</span>
                  <el-input v-model="assertion.description" placeholder="断言说明（可选）" />
                </div>
              </el-card>
              <div v-if="!(element.assertions?.length)" class="assertion-empty">暂无断言</div>
            </div>
          </div>
        </el-card>
      </template>
    </Draggable>
    <div class="add-more">
      <el-button type="primary" plain class="w-full" @click="addStep">+ 添加操作</el-button>
    </div>
  </div>
</template>

<style scoped>
.mb16 { margin-bottom: 16px; }
.content-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.phase-card { border-left: 3px solid var(--primary); }
.phase-card.tone-warning { border-left-color: var(--warning); }
.phase-card.tone-success { border-left-color: var(--success); }
.section-title-row { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 14px; }
.section-title { font-size: 15px; font-weight: 600; color: var(--text); }
.section-description { margin-top: 4px; color: var(--text-2); font-size: 12px; }
.step-list { display: flex; flex-direction: column; gap: 8px; }
.step-card { border-radius: 8px; }
.step-card.collapsed :deep(.el-card__body) { padding-top: 10px; padding-bottom: 10px; }
.step-head { display: flex; align-items: center; gap: 12px; }
.step-head.collapsed { cursor: pointer; }
.step-head.collapsed:focus-visible { outline: 2px solid var(--primary); outline-offset: 4px; border-radius: 4px; }
.drag-handle { cursor: move; color: #999; }
.drag-handle:hover { color: var(--primary); }
.step-badge {
  width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-size: 12px; font-weight: 600; flex-shrink: 0;
  background: var(--primary-light); color: var(--primary);
}
.action-select { flex: 1; max-width: 220px; }
.step-summary { display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0; }
.step-action-label { color: var(--text); font-weight: 600; flex-shrink: 0; }
.step-summary-text {
  color: var(--text-2);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.continue-label { color: #888; font-size: 13px; flex-shrink: 0; }
.step-body { margin-top: 8px; }
.assertion-section { margin-top: 14px; padding-top: 14px; border-top: 1px dashed var(--border); }
.assertion-title-row, .assertion-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.assertion-tip { margin-left: 10px; color: var(--text-2); font-size: 12px; }
.assertion-card { margin-top: 10px; background: #fafbff; }
.assertion-head { justify-content: flex-start; margin-bottom: 10px; }
.assertion-head .action-select { flex: 1; }
.assertion-badge { color: var(--primary); font-weight: 600; }
.assertion-empty { padding: 14px 0 2px; color: var(--text-2); text-align: center; font-size: 13px; }
.step-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.field-label { width: 80px; color: #888; flex-shrink: 0; }
.w-200 { width: 200px; }
.add-more { margin-top: 10px; }
.add-more .w-full { width: 100%; border-style: dashed; }
@media (max-width: 768px) {
  .step-head { gap: 8px; }
  .step-summary { gap: 8px; }
}
</style>
