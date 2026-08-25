<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  listProfileOverrides,
  restoreElementOverride,
  restoreVariableOverride,
  upsertElementOverride,
  upsertVariableOverride,
} from '@/api/appProfiles'
import { listElements, LOCATOR_TYPES, type TestElement } from '@/api/elements'
import { listVariables, type Variable } from '@/api/suites'

const props = defineProps<{ profileId: number; projectId: number; revision: number }>()
const emit = defineEmits<{ revisionChange: [revision: number] }>()
const visible = defineModel<boolean>('modelValue')

const tab = ref<'element' | 'variable'>('element')
const elements = ref<TestElement[]>([])
const variables = ref<Variable[]>([])
const elementDrafts = ref<Record<number, { locator_type: string; locator_value: string }>>({})
const variableDrafts = ref<Record<string, { value: string; description: string }>>({})
const overriddenElements = ref<Set<number>>(new Set())
const overriddenVariables = ref<Set<string>>(new Set())
const currentRevision = ref(props.revision)
const loading = ref(false)
const savingKey = ref('')

async function load() {
  if (!visible.value) return
  loading.value = true
  try {
    const [elementPage, projectVariables, globalVariables, overrides] = await Promise.all([
      listElements({ project_id: props.projectId, page_size: 200 }),
      listVariables({ scope: 'project', project_id: props.projectId }),
      listVariables({ scope: 'global' }),
      listProfileOverrides(props.profileId),
    ])
    elements.value = elementPage.items
    const variableMap = new Map<string, Variable>()
    for (const variable of [...globalVariables, ...projectVariables]) variableMap.set(variable.name, variable)
    variables.value = [...variableMap.values()]

    const elementMap = new Map(overrides.elements.map((item) => [item.element_id, item]))
    elementDrafts.value = Object.fromEntries(elements.value.map((element) => {
      const value = elementMap.get(element.id)
      return [element.id, {
        locator_type: value?.locator_type ?? element.locator_type,
        locator_value: value?.locator_value ?? element.locator_value,
      }]
    }))
    overriddenElements.value = new Set(overrides.elements.map((item) => item.element_id))

    const variableMapOverride = new Map(overrides.variables.map((item) => [item.name, item]))
    variableDrafts.value = Object.fromEntries(variables.value.map((variable) => {
      const value = variableMapOverride.get(variable.name)
      return [variable.name, {
        value: value?.value ?? variable.value,
        description: value?.description ?? variable.description ?? '',
      }]
    }))
    overriddenVariables.value = new Set(overrides.variables.map((item) => item.name))
    currentRevision.value = overrides.revision
  } finally {
    loading.value = false
  }
}

function revisionChanged(revision: number) {
  currentRevision.value = revision
  emit('revisionChange', revision)
}

async function saveElement(elementId: number) {
  const element = elements.value.find((item) => item.id === elementId)
  if (!element) return
  const draft = elementDrafts.value[element.id]
  if (!draft?.locator_type || !draft.locator_value.trim()) {
    ElMessage.warning('定位类型和定位值不能为空')
    return
  }
  savingKey.value = `element:${element.id}`
  try {
    const result = await upsertElementOverride(props.profileId, element.id, {
      expected_revision: currentRevision.value,
      locator_type: draft.locator_type,
      locator_value: draft.locator_value,
    })
    overriddenElements.value = new Set(overriddenElements.value).add(element.id)
    revisionChanged(result.revision)
    ElMessage.success('元素覆盖已保存')
  } finally {
    savingKey.value = ''
  }
}

async function restoreElement(elementId: number) {
  const element = elements.value.find((item) => item.id === elementId)
  if (!element) return
  savingKey.value = `element:${element.id}`
  try {
    await restoreElementOverride(props.profileId, element.id, { expected_revision: currentRevision.value })
    await load()
    revisionChanged(currentRevision.value)
    ElMessage.success('已恢复公共定位')
  } finally {
    savingKey.value = ''
  }
}

async function saveVariable(variableName: string) {
  const variable = variables.value.find((item) => item.name === variableName)
  if (!variable) return
  const draft = variableDrafts.value[variable.name]
  if (!draft) return
  savingKey.value = `variable:${variable.name}`
  try {
    const result = await upsertVariableOverride(props.profileId, variable.name, {
      expected_revision: currentRevision.value,
      value: draft.value,
      description: draft.description || undefined,
    })
    overriddenVariables.value = new Set(overriddenVariables.value).add(variable.name)
    revisionChanged(result.revision)
    ElMessage.success('变量覆盖已保存')
  } finally {
    savingKey.value = ''
  }
}

async function restoreVariable(variableName: string) {
  const variable = variables.value.find((item) => item.name === variableName)
  if (!variable) return
  savingKey.value = `variable:${variable.name}`
  try {
    await restoreVariableOverride(props.profileId, variable.name, { expected_revision: currentRevision.value })
    await load()
    revisionChanged(currentRevision.value)
    ElMessage.success('已恢复公共变量')
  } finally {
    savingKey.value = ''
  }
}

watch([visible, () => props.profileId], load, { immediate: true })
watch(() => props.revision, (revision) => { currentRevision.value = revision })
</script>

<template>
  <el-drawer v-model="visible" title="覆盖配置" size="680px">
    <el-alert type="info" :closable="false" show-icon title="这里只保存与公共资产的差异；恢复后将自动跟随公共配置。" />
    <el-tabs v-model="tab" class="override-tabs">
      <el-tab-pane label="元素覆盖" name="element">
        <el-table :data="elements" v-loading="loading" size="small" max-height="520">
          <el-table-column prop="name" label="元素" min-width="110" />
          <el-table-column label="定位类型" width="160">
            <template #default="{ row }">
              <el-select v-model="elementDrafts[row.id].locator_type" size="small" filterable allow-create>
                <el-option v-for="item in LOCATOR_TYPES" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="定位值" min-width="210">
            <template #default="{ row }"><el-input v-model="elementDrafts[row.id].locator_value" size="small" /></template>
          </el-table-column>
          <el-table-column label="操作" width="125" align="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" text :loading="savingKey === `element:${row.id}`" @click="saveElement(row.id)">保存</el-button>
              <el-button v-if="overriddenElements.has(row.id)" size="small" text @click="restoreElement(row.id)">恢复</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="变量覆盖" name="variable">
        <el-table :data="variables" v-loading="loading" size="small" max-height="520">
          <el-table-column prop="name" label="变量" min-width="120" />
          <el-table-column prop="scope" label="来源" width="80" />
          <el-table-column label="覆盖值" min-width="230">
            <template #default="{ row }"><el-input v-model="variableDrafts[row.name].value" size="small" /></template>
          </el-table-column>
          <el-table-column label="操作" width="125" align="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" text :loading="savingKey === `variable:${row.name}`" @click="saveVariable(row.name)">保存</el-button>
              <el-button v-if="overriddenVariables.has(row.name)" size="small" text @click="restoreVariable(row.name)">恢复</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-drawer>
</template>

<style scoped>
.override-tabs { margin-top: 12px; }
</style>
