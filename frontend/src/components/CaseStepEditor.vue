<script setup lang="ts">
import Draggable from 'vuedraggable'

import {
  ACTIONS,
  actionMeta,
  defaultParams,
  type Step,
  type StepPhase,
} from '@/api/cases'
import ElementSelector from '@/components/ElementSelector.vue'

const props = defineProps<{
  modelValue: Step[]
  phase: StepPhase
  title: string
  description: string
  tone?: 'primary' | 'warning' | 'success'
}>()
const emit = defineEmits<{ 'update:modelValue': [steps: Step[]] }>()

function update(steps: Step[]) {
  emit(
    'update:modelValue',
    steps.map((step, index) => ({ ...step, phase: props.phase, order: index + 1 })),
  )
}

function addStep() {
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
    },
  ])
}

function removeStep(index: number) {
  update(props.modelValue.filter((_, current) => current !== index))
}

function onActionChange(step: Step) {
  step.params = defaultParams(actionMeta(step.action).fields)
  update([...props.modelValue])
}

function onDragEnd() {
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
      item-key="order"
      handle=".drag-handle"
      class="step-list"
      @update:model-value="update"
      @end="onDragEnd"
    >
      <template #item="{ element, index }">
        <el-card class="step-card" shadow="never">
          <div class="step-head">
            <span class="drag-handle">⠿</span>
            <span class="step-badge">{{ index + 1 }}</span>
            <el-select v-model="element.action" class="action-select" @change="onActionChange(element)">
              <el-option v-for="action in ACTIONS" :key="action.value" :label="action.label" :value="action.value" />
            </el-select>
            <span class="continue-label">失败后继续</span>
            <el-switch v-model="element.continue_on_failure" size="small" @change="update([...modelValue])" />
            <el-button type="danger" text size="small" @click="removeStep(index)">删除</el-button>
          </div>
          <div class="step-body">
            <div v-if="actionMeta(element.action).needsElement" class="step-row">
              <span class="field-label">元素</span>
              <ElementSelector v-model="element.element_id" />
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
          </div>
        </el-card>
      </template>
    </Draggable>
    <el-empty v-if="modelValue.length === 0" :description="`暂无${title}`" :image-size="48" />
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
.step-head { display: flex; align-items: center; gap: 12px; }
.drag-handle { cursor: move; color: #999; }
.drag-handle:hover { color: var(--primary); }
.step-badge {
  width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-size: 12px; font-weight: 600; flex-shrink: 0;
  background: var(--primary-light); color: var(--primary);
}
.action-select { flex: 1; max-width: 220px; }
.continue-label { color: #888; font-size: 13px; flex-shrink: 0; }
.step-body { margin-top: 8px; }
.step-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.field-label { width: 80px; color: #888; flex-shrink: 0; }
.w-200 { width: 200px; }
.add-more { margin-top: 10px; }
.add-more .w-full { width: 100%; border-style: dashed; }
</style>
