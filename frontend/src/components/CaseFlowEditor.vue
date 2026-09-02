<script setup lang="ts">
import { ref, toRaw, watch } from 'vue'
import Draggable from 'vuedraggable'

import {
  ACTIONS,
  ASSERTION_TYPES,
  actionMeta,
  assertionMeta,
  defaultParams,
  type ActionNode,
  type AssertionNode,
  type FlowNode,
  type StepPhase,
} from '@/api/cases'
import ElementSelector from '@/components/ElementSelector.vue'
import { stepActionLabel, stepSummaryText, type ElementNameMap } from '@/utils/stepEditorSummary'
import { createUuid } from '@/utils/uuid'

const props = defineProps<{
  modelValue: FlowNode[]
  phase: StepPhase
  title: string
  description: string
  tone?: 'primary' | 'warning' | 'success'
  projectId?: number
  elementNames?: ElementNameMap
}>()
const emit = defineEmits<{ 'update:modelValue': [nodes: FlowNode[]] }>()
const nodeUiKeyIds = new Map<string, number>()
const nodeUiIds = new WeakMap<object, number>()
const collapsedIds = ref(new Set<number>())
let nextUiId = 0
let collapseApplied = false

watch(() => props.modelValue, (nodes) => {
  if (!collapseApplied && nodes.length) {
    collapseApplied = true
    collapsedIds.value = new Set(nodes.map((node) => nodeUiId(node)))
  }
})

function nodeUiId(node: FlowNode): number {
  if (node.key) {
    const existingByKey = nodeUiKeyIds.get(node.key)
    if (existingByKey != null) return existingByKey
    const createdByKey = ++nextUiId
    nodeUiKeyIds.set(node.key, createdByKey)
    return createdByKey
  }
  const raw = toRaw(node)
  const existing = nodeUiIds.get(raw)
  if (existing != null) return existing
  const id = ++nextUiId
  nodeUiIds.set(raw, id)
  return id
}
function isCollapsed(node: FlowNode) { return collapsedIds.value.has(nodeUiId(node)) }
function toggle(node: FlowNode) {
  const id = nodeUiId(node)
  if (collapsedIds.value.has(id)) collapsedIds.value.delete(id)
  else collapsedIds.value.add(id)
}
function update(nodes: FlowNode[]) {
  emit('update:modelValue', nodes.map((node, index) => ({ ...node, phase: props.phase, order: index + 1 } as FlowNode)))
}
function addAction() {
  collapseApplied = true
  const node: ActionNode = {
    kind: 'action', key: createUuid(), order: props.modelValue.length + 1, phase: props.phase,
    action: 'click', element_id: null, params: defaultParams(actionMeta('click').fields),
    description: '', continue_on_failure: false,
  }
  update([...props.modelValue, node])
}
function addAssertion() {
  collapseApplied = true
  const node: AssertionNode = {
    kind: 'assertion', key: createUuid(), order: props.modelValue.length + 1, phase: props.phase,
    type: 'element_exists', element_id: null, params: defaultParams(assertionMeta('element_exists').fields),
    description: '', max_wait_seconds: 10, continue_on_failure: false,
  }
  update([...props.modelValue, node])
}
function remove(node: FlowNode) {
  collapsedIds.value.delete(nodeUiId(node))
  update(props.modelValue.filter((candidate) => {
    if (candidate === node) return false
    return !node.key || candidate.key !== node.key
  }))
}
function onActionChange(node: ActionNode) { node.params = defaultParams(actionMeta(node.action).fields); update([...props.modelValue]) }
function onAssertionTypeChange(node: AssertionNode) { node.params = defaultParams(assertionMeta(node.type).fields); update([...props.modelValue]) }
function nodeLabel(node: FlowNode) { return node.kind === 'action' ? stepActionLabel(node) : assertionMeta(node.type).label }
function nodeSummary(node: FlowNode) { return node.kind === 'action' ? stepSummaryText(node, props.elementNames) : (node.description || `最大等待 ${node.max_wait_seconds}s`) }
</script>

<template>
  <div class="content-card mb16 phase-card" :class="`tone-${tone ?? 'primary'}`">
    <div class="section-title-row">
      <div><div class="section-title">{{ title }}</div><div class="section-description">{{ description }}</div></div>
      <div class="node-actions"><el-button type="primary" plain size="small" @click="addAction">添加操作</el-button><el-button type="warning" plain size="small" @click="addAssertion">添加断言</el-button></div>
    </div>
    <Draggable :model-value="modelValue" :item-key="nodeUiId" handle=".drag-handle" class="step-list" group="case-flow" @update:model-value="update" @end="() => update([...modelValue])">
      <template #item="{ element, index }">
        <el-card class="step-card" :class="{ collapsed: isCollapsed(element) }" shadow="never">
          <div class="step-head" :class="{ collapsed: isCollapsed(element) }" @click="isCollapsed(element) && toggle(element)">
            <span class="drag-handle" @click.stop>⠿</span><span class="step-badge" :class="element.kind">{{ index + 1 }}</span>
            <div v-if="isCollapsed(element)" class="step-summary"><span class="step-action-label">{{ nodeLabel(element) }}</span><span class="step-summary-text">{{ nodeSummary(element) }}</span></div>
            <template v-else>
              <el-select v-if="element.kind === 'action'" v-model="element.action" class="action-select" @change="onActionChange(element)"><el-option v-for="item in ACTIONS" :key="item.value" :label="item.label" :value="item.value" /></el-select>
              <el-select v-else v-model="element.type" class="action-select" @change="onAssertionTypeChange(element)"><el-option v-for="item in ASSERTION_TYPES" :key="item.value" :label="item.label" :value="item.value" /></el-select>
            </template>
            <template v-if="!isCollapsed(element)"><span class="continue-label">失败后继续</span><el-switch v-model="element.continue_on_failure" size="small" @change="update([...modelValue])" /></template>
            <el-button text size="small" @click.stop="toggle(element)">{{ isCollapsed(element) ? '展开' : '收起' }}</el-button><el-button type="danger" text size="small" @click.stop="remove(element)">删除</el-button>
          </div>
          <div v-show="!isCollapsed(element)" class="step-body">
            <template v-if="element.kind === 'action'">
              <div v-if="actionMeta(element.action).needsElement" class="step-row"><span class="field-label">{{ actionMeta(element.action).elementLabel ?? '元素' }}</span><ElementSelector v-model="element.element_id" :project-id="projectId" /></div>
              <div v-for="field in actionMeta(element.action).fields" :key="field.key" class="step-row"><span class="field-label">{{ field.label }}</span><ElementSelector v-if="field.type === 'element'" v-model="element.params![field.key]" :project-id="projectId" /><el-select v-else-if="field.type === 'select'" v-model="element.params![field.key]" class="w-200"><el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" /></el-select><el-switch v-else-if="field.type === 'switch'" v-model="element.params![field.key]" /><el-input v-else v-model="element.params![field.key]" :type="field.type === 'number' ? 'number' : 'text'" :min="field.min" :max="field.max" :placeholder="field.placeholder" class="w-200" /></div>
            </template>
            <template v-else>
              <div v-if="assertionMeta(element.type).needsElement" class="step-row"><span class="field-label">元素</span><ElementSelector v-model="element.element_id" :project-id="projectId" /></div>
              <div v-for="field in assertionMeta(element.type).fields" :key="field.key" class="step-row"><span class="field-label">{{ field.label }}</span><el-select v-if="field.type === 'select'" v-model="element.params![field.key]" class="w-200"><el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" /></el-select><el-switch v-else-if="field.type === 'switch'" v-model="element.params![field.key]" /><el-input v-else v-model="element.params![field.key]" :type="field.type === 'number' ? 'number' : 'text'" :placeholder="field.placeholder" class="w-200" /></div>
              <div class="step-row"><span class="field-label">最大等待(s)</span><el-input v-model.number="element.max_wait_seconds" type="number" min="0" max="300" class="w-200" /></div>
            </template>
            <div class="step-row"><span class="field-label">描述</span><el-input v-model="element.description" placeholder="说明（可选）" /></div>
          </div>
        </el-card>
      </template>
    </Draggable>
    <div class="add-more"><el-button type="primary" plain class="w-full" @click="addAction">+ 添加操作</el-button><el-button type="warning" plain class="w-full" @click="addAssertion">+ 添加断言</el-button></div>
  </div>
</template>

<style scoped>
.mb16{margin-bottom:16px}.content-card{background:#fff;border:1px solid var(--border);border-radius:12px;padding:20px}.phase-card{border-left:3px solid var(--primary)}.tone-warning{border-left-color:var(--warning)}.tone-success{border-left-color:var(--success)}.section-title-row,.node-actions,.step-head,.step-summary{display:flex;align-items:center;gap:10px}.section-title-row{justify-content:space-between;align-items:flex-start;margin-bottom:14px}.section-title{font-size:15px;font-weight:600}.section-description{margin-top:4px;color:var(--text-2);font-size:12px}.step-list{display:flex;flex-direction:column;gap:8px}.step-card{border-radius:8px}.step-head.collapsed{cursor:pointer}.drag-handle{cursor:move;color:#999}.step-badge{width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:12px;background:var(--primary-light);color:var(--primary)}.step-badge.assertion{background:#fff1e6;color:#d97706}.action-select{flex:1;max-width:240px}.step-summary{flex:1;min-width:0}.step-action-label{font-weight:600;flex-shrink:0}.step-summary-text{color:var(--text-2);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.continue-label{color:#888;font-size:13px}.step-body{margin-top:8px}.step-row{display:flex;align-items:center;gap:8px;margin-bottom:6px}.field-label{width:90px;color:#888;flex-shrink:0}.w-200{width:200px}.add-more{display:flex;gap:8px;margin-top:10px}.w-full{width:100%;border-style:dashed}
</style>
