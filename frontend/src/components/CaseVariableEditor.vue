<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  applySameValue,
  buildOccurrenceUpdates,
  diffNodeEntries,
  variableStatusMeta,
  variableToken,
  type EditorVariable,
} from '@/utils/caseVariables'

export interface VariableSavePayload {
  kind: 'occurrence' | 'nodes'
  updates: Record<string, string | null> | ReturnType<typeof diffNodeEntries>
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    mode: 'occurrence' | 'nodes'
    title: string
    subtitle?: string
    variables?: EditorVariable[]
    loading?: boolean
    saving?: boolean
    readonly?: boolean
    context?: { name?: string; action?: string; order?: number | null; element?: string | null }
  }>(),
  {
    variables: () => [],
    loading: false,
    saving: false,
    readonly: false,
    subtitle: '',
    context: undefined,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [payload: VariableSavePayload]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const occurrenceState = reactive<Record<string, { enabled: boolean; value: string }>>({})
const nodeState = reactive<Record<string, { node_key: string; node_type: 'step' | 'assertion'; name: string; enabled: boolean; value: string }[]>>({})
const sharedValue = reactive<Record<string, string>>({})

function resetState() {
  for (const key of Object.keys(occurrenceState)) delete occurrenceState[key]
  for (const key of Object.keys(nodeState)) delete nodeState[key]
  for (const key of Object.keys(sharedValue)) delete sharedValue[key]
  for (const variable of props.variables) {
    occurrenceState[variable.name] = {
      enabled: Boolean(variable.override_enabled),
      value: variable.override_value ?? '',
    }
    nodeState[variable.name] = (variable.references ?? []).map((ref) => ({
      node_key: ref.node_key,
      node_type: ref.node_type,
      name: variable.name,
      enabled: ref.override_enabled,
      value: ref.override_value ?? '',
    }))
    sharedValue[variable.name] = ''
  }
}

watch(
  () => props.variables,
  () => resetState(),
  { deep: true },
)

watch(
  () => props.modelValue,
  (value) => {
    if (value) resetState()
  },
)

function applyShared(variable: EditorVariable) {
  const value = sharedValue[variable.name]
  const next = applySameValue(variable.references ?? [], variable.name, value ?? '')
  nodeState[variable.name] = next
}

function onSave() {
  if (props.mode === 'occurrence') {
    emit('save', {
      kind: 'occurrence',
      updates: buildOccurrenceUpdates(props.variables, occurrenceState),
    })
    return
  }
  const updates: ReturnType<typeof diffNodeEntries> = []
  for (const variable of props.variables) {
    updates.push(...diffNodeEntries(variable.references ?? [], nodeState[variable.name] ?? []))
  }
  if (updates.length === 0) {
    ElMessage.info('没有需要保存的变更')
    return
  }
  emit('save', { kind: 'nodes', updates })
}

function enabledCount(variable: EditorVariable): number {
  if (props.mode === 'occurrence') return occurrenceState[variable.name]?.enabled ? 1 : 0
  return (nodeState[variable.name] ?? []).filter((entry) => entry.enabled).length
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="title"
    width="min(680px, calc(100vw - 32px))"
    append-to-body
    class="case-variable-editor"
  >
    <template #header>
      <div class="editor-header">
        <div class="editor-title">{{ title }}</div>
        <div v-if="subtitle" class="editor-subtitle">{{ subtitle }}</div>
      </div>
    </template>

    <div v-if="context" class="editor-context">
      <span class="context-kicker">当前用例</span>
      <strong class="context-name" :title="context.name">{{ context.name || '—' }}</strong>
      <span v-if="context.action" class="context-action">{{ context.action }}</span>
      <span v-if="context.order != null" class="context-action">第 {{ context.order }} 项</span>
    </div>

    <div class="editor-hint" role="note">
      覆盖只作用于当前
      {{ mode === 'occurrence' ? '套件编排项' : 'APP 档案节点' }}；未启用的变量继续继承原值，执行参数优先级仍高于此处。
    </div>

    <div v-loading="loading" class="editor-body">
      <div v-if="!loading && variables.length === 0" class="editor-empty">该用例没有参数变量</div>

      <section v-for="variable in variables" :key="variable.name" class="variable-group">
        <header class="group-head">
          <code class="variable-name">{{ variableToken(variable.name) }}</code>
          <span class="variable-state" :class="`tone-${variableStatusMeta(variable.status).tone}`">
            {{ variableStatusMeta(variable.status).label }}
          </span>
          <span class="group-meta">
            引用 {{ variable.reference_count }} 处
            <template v-if="variable.inherited_scope">· 继承自{{ variable.inherited_scope }}</template>
          </span>
          <span class="group-count">已启用 {{ enabledCount(variable) }}</span>
        </header>

        <template v-if="mode === 'occurrence'">
          <div class="variable-row">
            <el-input
              v-model="occurrenceState[variable.name].value"
              class="variable-input"
              :disabled="readonly || !occurrenceState[variable.name]?.enabled"
              :placeholder="occurrenceState[variable.name]?.enabled ? '输入编排项覆盖值（可为空）' : '启用覆盖后输入值'"
              :aria-label="`${variable.name} 的编排项覆盖值`"
            />
            <el-switch
              v-model="occurrenceState[variable.name].enabled"
              :disabled="readonly"
              :aria-label="`启用 ${variable.name} 覆盖`"
            />
          </div>
        </template>

        <template v-else>
          <div class="shared-row">
            <el-input
              v-model="sharedValue[variable.name]"
              class="variable-input"
              :disabled="readonly"
              placeholder="全部设为同一值（可选）"
              :aria-label="`${variable.name} 的全部节点统一值`"
            />
            <el-button :disabled="readonly" size="small" @click="applyShared(variable)">应用到全部节点</el-button>
          </div>
          <div class="reference-list">
            <div
              v-for="entry in nodeState[variable.name]"
              :key="entry.node_key"
              class="variable-row reference-row"
            >
              <span class="reference-node" :title="entry.node_key">
                <span class="node-type">{{ entry.node_type === 'assertion' ? '断言' : '动作' }}</span>
                <span class="node-name">
                  {{ (variable.references ?? []).find((ref) => ref.node_key === entry.node_key)?.node_name || entry.node_key }}
                </span>
              </span>
              <el-input
                v-model="entry.value"
                class="variable-input"
                :disabled="readonly || !entry.enabled"
                :placeholder="entry.enabled ? '输入该节点覆盖值（可为空）' : '启用覆盖后输入值'"
                :aria-label="`${variable.name} 在 ${entry.node_key} 的覆盖值`"
              />
              <el-switch v-model="entry.enabled" :disabled="readonly" :aria-label="`启用 ${variable.name} 在 ${entry.node_key} 的覆盖`" />
            </div>
          </div>
        </template>
      </section>
    </div>

    <template #footer>
      <div class="editor-footer">
        <el-button @click="visible = false">取消</el-button>
        <el-button v-if="!readonly" type="primary" :loading="saving" @click="onSave">保存覆盖</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.editor-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.editor-title {
  color: var(--el-text-color-primary);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.35;
}
.editor-subtitle {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.editor-context {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
}
.context-kicker,
.context-action {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.context-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.editor-hint {
  margin-top: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.editor-body {
  margin-top: 12px;
  max-height: min(52vh, 460px);
  overflow-y: auto;
  padding-right: 2px;
}
.editor-empty {
  padding: 28px 0;
  text-align: center;
  color: var(--el-text-color-secondary);
}
.variable-group {
  padding: 10px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}
.group-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.variable-name {
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
  color: var(--el-color-primary);
}
.variable-state {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  border: 1px solid transparent;
}
.tone-overridden { color: #1d4ed8; background: rgba(37, 99, 235, 0.12); border-color: rgba(37, 99, 235, 0.35); }
.tone-inherited { color: var(--el-text-color-secondary); background: rgba(100, 116, 139, 0.1); border-color: rgba(100, 116, 139, 0.28); }
.tone-undefined { color: #c2410c; background: rgba(249, 115, 22, 0.12); border-color: rgba(249, 115, 22, 0.35); }
.tone-random { color: #7c3aed; background: rgba(139, 92, 246, 0.14); border-color: rgba(139, 92, 246, 0.38); }
.tone-mixed { color: #b45309; background: rgba(245, 158, 11, 0.14); border-color: rgba(245, 158, 11, 0.4); }
.group-meta,
.group-count {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.group-count {
  margin-left: auto;
}
.variable-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.variable-input {
  flex: 1;
  min-width: 120px;
}
.shared-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.reference-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.reference-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 190px;
  min-width: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.node-type {
  flex-shrink: 0;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--el-fill-color-light);
}
.node-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
