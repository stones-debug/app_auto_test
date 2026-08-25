<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { listElements } from '@/api/elements'
import { listVariables } from '@/api/suites'
import { upsertElementOverride, restoreElementOverride, upsertVariableOverride, restoreVariableOverride } from '@/api/appProfiles'
import { ElMessage } from 'element-plus'

const props = defineProps<{ profileId: number; projectId: number; revision: number }>()
const visible = defineModel<boolean>('modelValue')

const tab = ref<'element' | 'variable'>('element')
const elements = ref<{ id: number; name: string; locator_type: string; locator_value: string }[]>([])
const variables = ref<{ id: number; name: string; value: string }[]>([])
const elementOverrides = ref<Record<number, { locator_type: string; locator_value: string }>>({})
const variableOverrides = ref<Record<number, { value: string }>>({})
const loading = ref(false)

async function loadElements() {
  const page = await listElements({ project_id: props.projectId, page_size: 200 })
  elements.value = page.items.map((e) => ({ id: e.id, name: e.name, locator_type: (e as { locator_type: string }).locator_type, locator_value: (e as { locator_value: string }).locator_value }))
}

async function loadVariables() {
  const page = await listVariables({ scope: 'project', project_id: props.projectId })
  variables.value = page.map((v) => ({ id: v.id, name: v.name, value: v.value }))
}

async function load() {
  loading.value = true
  try {
    await Promise.all([loadElements(), loadVariables()])
  } finally {
    loading.value = false
  }
}

async function saveElement(id: number) {
  const ov = elementOverrides.value[id]
  if (!ov) return
  await upsertElementOverride(props.profileId, id, { expected_revision: props.revision, locator_type: ov.locator_type, locator_value: ov.locator_value })
  ElMessage.success('元素覆盖已保存')
}

async function restoreElement(id: number) {
  await restoreElementOverride(props.profileId, id, { expected_revision: props.revision })
  delete elementOverrides.value[id]
  ElMessage.success('已恢复公共定位')
}

async function saveVariable(id: number) {
  const name = variables.value.find((v) => v.id === id)?.name
  if (!name) return
  const value = variableOverrides.value[id]?.value ?? ''
  await upsertVariableOverride(props.profileId, name, { expected_revision: props.revision, value })
  ElMessage.success('变量覆盖已保存')
}

async function restoreVariable(id: number) {
  const name = variables.value.find((v) => v.id === id)?.name
  if (!name) return
  await restoreVariableOverride(props.profileId, name, { expected_revision: props.revision })
  delete variableOverrides.value[id]
  ElMessage.success('已恢复变量')
}

watch(() => props.profileId, load)
onMounted(load)

function ensureElement(id: number): { locator_type: string; locator_value: string } {
  return (elementOverrides.value[id] ??= { locator_type: '', locator_value: '' })
}

function elementValue(id: number): string {
  return ensureElement(id).locator_value
}

function setElementValue(id: number, v: string) {
  ensureElement(id).locator_value = v
}

function ensureVariable(id: number): { value: string } {
  return (variableOverrides.value[id] ??= { value: '' })
}

function variableValue(id: number): string {
  return ensureVariable(id).value
}

function setVariableValue(id: number, v: string) {
  ensureVariable(id).value = v
}
</script>

<template>
  <el-drawer v-model="visible" title="覆盖配置" size="560px">
    <el-tabs v-model="tab">
      <el-tab-pane label="元素覆盖" name="element">
        <el-table :data="elements" v-loading="loading" size="small" max-height="440">
          <el-table-column prop="name" label="元素" min-width="120" />
          <el-table-column label="定位覆盖" min-width="180">
            <template #default="{ row }">
              <el-input
                :model-value="elementValue(row.id)"
                @update:model-value="(v: string) => setElementValue(row.id, v)"
                size="small"
                :placeholder="row.locator_value"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" text @click="saveElement(row.id)">保存</el-button>
              <el-button v-if="elementOverrides[row.id]" size="small" text @click="restoreElement(row.id)">恢复</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="变量覆盖" name="variable">
        <el-table :data="variables" size="small" max-height="440">
          <el-table-column prop="name" label="变量" min-width="120" />
          <el-table-column label="覆盖值" min-width="180">
            <template #default="{ row }">
              <el-input
                :model-value="variableValue(row.id)"
                @update:model-value="(v: string) => setVariableValue(row.id, v)"
                size="small"
                placeholder="公共值"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" text @click="saveVariable(row.id)">保存</el-button>
              <el-button v-if="variableOverrides[row.id]" size="small" text @click="restoreVariable(row.id)">恢复</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-drawer>
</template>
