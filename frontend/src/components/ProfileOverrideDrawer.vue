<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  listProfileOverrides,
  restoreElementOverride,
  restoreVariableOverride,
  upsertElementOverride,
  upsertVariableOverride,
  type ProfileOverrides,
} from '@/api/appProfiles'
import { listElements, LOCATOR_TYPES, type TestElement } from '@/api/elements'
import { listVariables, type Variable } from '@/api/suites'
import SmartLocatorEditor from '@/components/SmartLocatorEditor.vue'
import { buildLocatorPayload, cloneSmartConfig, createDefaultConfig, smartLocatorSummary, validateSmartConfig, type SmartLocatorConfig } from '@/utils/smartLocator'

const props = defineProps<{ profileId: number; projectId: number; revision: number }>()
const emit = defineEmits<{ revisionChange: [revision: number] }>()
const visible = defineModel<boolean>('modelValue')

const ELEMENT_PAGE_SIZE = 20

const tab = ref<'element' | 'variable'>('element')
const elements = ref<TestElement[]>([])
const elementPage = ref(1)
const elementPageSize = ref(ELEMENT_PAGE_SIZE)
const elementTotal = ref(0)
const elementKeyword = ref('')
const variables = ref<Variable[]>([])
const elementDrafts = ref<Record<number, { locator_type: string; locator_value: string; locator_config: SmartLocatorConfig | null }>>({})
const variableDrafts = ref<Record<string, { value: string; description: string }>>({})
const overriddenElements = ref<Set<number>>(new Set())
const overriddenVariables = ref<Set<string>>(new Set())
const currentRevision = ref(props.revision)
const elementLoading = ref(false)
const variableLoading = ref(false)
const elementError = ref('')
const variableError = ref('')
const savingKey = ref('')

// 覆盖配置在同一个档案内只请求一次；变量页首次打开时复用该结果。
let overridesCache: { profileId: number; value: ProfileOverrides } | null = null
let overridesPromise: { profileId: number; value: Promise<ProfileOverrides> } | null = null
let elementRequestSequence = 0
let variableRequestSequence = 0
let profileGeneration = 0
let variablesLoadedForProfile: number | null = null

const configDialogVisible = ref(false)
const configDialogElementId = ref<number | null>(null)
const configDraft = ref<SmartLocatorConfig | null>(null)

function isCurrent(profileId: number, requestId: number, kind: 'element' | 'variable') {
  return visible.value
    && props.profileId === profileId
    && (kind === 'element' ? elementRequestSequence : variableRequestSequence) === requestId
}

function errorText(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : ''
  return message && !message.includes('Network Error') ? `${fallback}：${message}` : fallback
}

function applyElementOverrides(items: TestElement[], overrides: ProfileOverrides) {
  const overrideMap = new Map(overrides.elements.map((item) => [item.element_id, item]))
  const nextDrafts = { ...elementDrafts.value }
  for (const element of items) {
    const value = overrideMap.get(element.id)
    nextDrafts[element.id] = {
      locator_type: value?.locator_type ?? element.locator_type,
      locator_value: value?.locator_value ?? element.locator_value ?? '',
      locator_config: value?.locator_config ?? element.locator_config ?? null,
    }
  }
  elementDrafts.value = nextDrafts
  overriddenElements.value = new Set(overrides.elements.map((item) => item.element_id))
}

function applyVariables(items: Variable[], overrides: ProfileOverrides) {
  const overrideMap = new Map(overrides.variables.map((item) => [item.name, item]))
  variableDrafts.value = Object.fromEntries(items.map((variable) => {
    const value = overrideMap.get(variable.name)
    return [variable.name, {
      value: value?.value ?? variable.value,
      description: value?.description ?? variable.description ?? '',
    }]
  }))
  overriddenVariables.value = new Set(overrides.variables.map((item) => item.name))
}

function variableSourceSummary(variable: Variable): string {
  if (variable.kind === 'random_integer') return `随机整数[${variable.spec?.min},${variable.spec?.max}]`
  if (variable.kind === 'random_choice') return `随机列表${variable.spec?.items?.length ?? 0}项`
  return variable.value || '固定空字符串'
}

async function ensureOverrides(profileId: number, force = false, generation = profileGeneration): Promise<ProfileOverrides> {
  if (!force && overridesCache?.profileId === profileId) return overridesCache.value
  if (!force && overridesPromise?.profileId === profileId) return overridesPromise.value

  const value = listProfileOverrides(profileId, { include_nodes: false }).then((result) => {
    if (props.profileId === profileId && visible.value && generation === profileGeneration) {
      overridesCache = { profileId, value: result }
      currentRevision.value = result.revision
    }
    return result
  })
  overridesPromise = { profileId, value }
  try {
    return await value
  } finally {
    if (overridesPromise?.value === value) overridesPromise = null
  }
}

async function loadElements(forceOverrides = false) {
  if (!visible.value) return
  const profileId = props.profileId
  const projectId = props.projectId
  const generation = profileGeneration
  const requestId = ++elementRequestSequence
  elementLoading.value = true
  elementError.value = ''
  try {
    const [page, overrides] = await Promise.all([
      listElements({
        project_id: projectId,
        page: elementPage.value,
        page_size: elementPageSize.value,
        keyword: elementKeyword.value.trim() || undefined,
      }),
      ensureOverrides(profileId, forceOverrides, generation),
    ])
    if (!isCurrent(profileId, requestId, 'element')) return
    elementTotal.value = page.total
    elements.value = page.items
    applyElementOverrides(page.items, overrides)
  } catch (error) {
    if (isCurrent(profileId, requestId, 'element')) elementError.value = errorText(error, '元素覆盖加载失败，请重试')
  } finally {
    if (isCurrent(profileId, requestId, 'element')) elementLoading.value = false
  }
}

async function loadVariables(forceOverrides = false) {
  if (!visible.value) return
  const profileId = props.profileId
  const projectId = props.projectId
  const generation = profileGeneration
  const requestId = ++variableRequestSequence
  variableLoading.value = true
  variableError.value = ''
  try {
    const [projectVariables, globalVariables, overrides] = await Promise.all([
      listVariables({ scope: 'project', project_id: projectId }),
      listVariables({ scope: 'global' }),
      ensureOverrides(profileId, forceOverrides, generation),
    ])
    if (!isCurrent(profileId, requestId, 'variable')) return
    const variableMap = new Map<string, Variable>()
    for (const variable of [...globalVariables, ...projectVariables]) variableMap.set(variable.name, variable)
    variables.value = [...variableMap.values()]
    applyVariables(variables.value, overrides)
    variablesLoadedForProfile = profileId
  } catch (error) {
    if (isCurrent(profileId, requestId, 'variable')) variableError.value = errorText(error, '变量覆盖加载失败，请重试')
  } finally {
    if (isCurrent(profileId, requestId, 'variable')) variableLoading.value = false
  }
}

function resetForProfile() {
  profileGeneration += 1
  elementRequestSequence += 1
  variableRequestSequence += 1
  elements.value = []
  variables.value = []
  elementDrafts.value = {}
  variableDrafts.value = {}
  overriddenElements.value = new Set()
  overriddenVariables.value = new Set()
  elementPage.value = 1
  elementTotal.value = 0
  elementError.value = ''
  variableError.value = ''
  variablesLoadedForProfile = null
  overridesCache = null
  currentRevision.value = props.revision
}

function revisionChanged(revision: number) {
  currentRevision.value = revision
  if (overridesCache?.profileId === props.profileId) overridesCache.value.revision = revision
  emit('revisionChange', revision)
}

function onTabChange(name: string | number) {
  if (name === 'variable' && variablesLoadedForProfile !== props.profileId) void loadVariables()
}

function searchElements() {
  elementPage.value = 1
  void loadElements()
}

function refreshElements() {
  void loadElements()
}

function onElementPageChange(page: number) {
  elementPage.value = page
  void loadElements()
}

function onElementPageSizeChange(size: number) {
  elementPageSize.value = size
  elementPage.value = 1
  void loadElements()
}

async function saveElement(elementId: number) {
  const element = elements.value.find((item) => item.id === elementId)
  if (!element) return
  const draft = elementDrafts.value[element.id]
  if (!draft?.locator_type) {
    ElMessage.warning('请选择定位类型')
    return
  }
  if (draft.locator_type === 'smart') {
    if (!draft.locator_config) {
      ElMessage.warning('请完成智能定位配置')
      return
    }
    const errors = validateSmartConfig(draft.locator_config)
    if (errors.length) {
      ElMessage.error('智能定位配置有误：' + errors[0])
      return
    }
  } else if (!draft.locator_value.trim()) {
    ElMessage.warning('定位类型和定位值不能为空')
    return
  }
  savingKey.value = `element:${element.id}`
  try {
    const result = await upsertElementOverride(props.profileId, element.id, {
      expected_revision: currentRevision.value,
      ...buildLocatorPayload(draft.locator_type, draft.locator_value, draft.locator_config),
    })
    overriddenElements.value = new Set(overriddenElements.value).add(element.id)
    if (overridesCache?.profileId === props.profileId) {
      const existing = overridesCache.value.elements.filter((item) => item.element_id !== element.id)
      overridesCache.value.elements = [...existing, {
        element_id: element.id,
        locator_type: draft.locator_type,
        locator_value: draft.locator_value || null,
        locator_config: draft.locator_config,
      }]
    }
    revisionChanged(result.revision)
    ElMessage.success('元素覆盖已保存')
  } catch (error) {
    ElMessage.error(errorText(error, '元素覆盖保存失败，请重试'))
  } finally {
    savingKey.value = ''
  }
}

function onLocatorTypeChange(elementId: number) {
  const draft = elementDrafts.value[elementId]
  if (!draft) return
  if (draft.locator_type === 'smart' && !draft.locator_config) draft.locator_config = createDefaultConfig()
}

function draftSummary(row: TestElement): string {
  const draft = elementDrafts.value[row.id]
  if (!draft) return ''
  if (draft.locator_type === 'smart') return smartLocatorSummary(draft.locator_config)
  return draft.locator_value || ''
}

function openConfigDialog(elementId: number) {
  configDialogElementId.value = elementId
  const draft = elementDrafts.value[elementId]
  configDraft.value = draft?.locator_config ? cloneSmartConfig(draft.locator_config) : createDefaultConfig()
  configDialogVisible.value = true
}

function saveConfigDialog() {
  const elementId = configDialogElementId.value
  if (elementId == null) return
  const errors = validateSmartConfig(configDraft.value)
  if (errors.length) {
    ElMessage.error('智能定位配置有误：' + errors[0])
    return
  }
  elementDrafts.value[elementId].locator_config = configDraft.value
  elementDrafts.value[elementId].locator_value = ''
  configDialogVisible.value = false
}

async function restoreElement(elementId: number) {
  const element = elements.value.find((item) => item.id === elementId)
  if (!element) return
  savingKey.value = `element:${element.id}`
  try {
    await restoreElementOverride(props.profileId, element.id, { expected_revision: currentRevision.value })
    await loadElements(true)
    revisionChanged(currentRevision.value)
    ElMessage.success('已恢复公共定位')
  } catch (error) {
    ElMessage.error(errorText(error, '恢复元素覆盖失败，请重试'))
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
    if (overridesCache?.profileId === props.profileId) {
      const existing = overridesCache.value.variables.filter((item) => item.name !== variableName)
      overridesCache.value.variables = [...existing, { name: variableName, value: draft.value, description: draft.description || null }]
    }
    revisionChanged(result.revision)
    ElMessage.success('变量覆盖已保存')
  } catch (error) {
    ElMessage.error(errorText(error, '变量覆盖保存失败，请重试'))
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
    await loadVariables(true)
    revisionChanged(currentRevision.value)
    ElMessage.success('已恢复公共变量')
  } catch (error) {
    ElMessage.error(errorText(error, '恢复变量覆盖失败，请重试'))
  } finally {
    savingKey.value = ''
  }
}

watch([visible, () => props.profileId, () => props.projectId], ([isVisible]) => {
  resetForProfile()
  if (!isVisible) return
  if (tab.value === 'element') void loadElements()
  else void loadVariables()
}, { immediate: true })
watch(() => props.revision, (revision) => { currentRevision.value = revision })
</script>

<template>
  <el-drawer v-model="visible" title="覆盖配置" size="680px" class="profile-override-drawer">
    <div class="override-content">
      <el-alert type="info" :closable="false" show-icon title="这里只保存与公共资产的差异；恢复后将自动跟随公共配置。" />
      <el-tabs v-model="tab" class="override-tabs" @tab-change="onTabChange">
        <el-tab-pane label="元素覆盖" name="element">
          <div class="tab-panel">
            <div class="table-toolbar">
              <el-input v-model="elementKeyword" clearable placeholder="搜索元素名称" class="element-search" @keyup.enter="searchElements" @clear="searchElements" />
              <el-button type="primary" plain @click="searchElements">搜索</el-button>
              <el-button @click="refreshElements">刷新</el-button>
            </div>
            <el-alert v-if="elementError" type="error" :closable="false" show-icon class="load-error">
              <template #title>{{ elementError }}</template>
              <el-button size="small" type="danger" plain @click="refreshElements">重试</el-button>
            </el-alert>
            <div class="table-shell">
              <el-empty v-if="elementLoading && !elements.length" description="加载中..." />
              <el-table v-else-if="elements.length" :data="elements" v-loading="elementLoading" size="small" height="100%">
                <el-table-column prop="name" label="元素" min-width="110" />
                <el-table-column label="定位类型" width="160">
                  <template #default="{ row }">
                    <el-select v-model="elementDrafts[row.id].locator_type" size="small" filterable allow-create @change="onLocatorTypeChange(row.id)">
                      <el-option v-for="item in LOCATOR_TYPES" :key="item.value" :label="item.label" :value="item.value" />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="定位值" min-width="210">
                  <template #default="{ row }">
                    <template v-if="elementDrafts[row.id].locator_type === 'smart'">
                      <el-tag size="small" type="primary" class="smart-tag" :title="draftSummary(row as TestElement)">{{ draftSummary(row as TestElement) }}</el-tag>
                      <el-button size="small" text type="primary" @click="openConfigDialog(row.id)">编辑配置</el-button>
                    </template>
                    <el-input v-else v-model="elementDrafts[row.id].locator_value" size="small" />
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="125" align="right">
                  <template #default="{ row }">
                    <el-button size="small" type="primary" text :loading="savingKey === `element:${row.id}`" @click="saveElement(row.id)">保存</el-button>
                    <el-button v-if="overriddenElements.has(row.id)" size="small" text @click="restoreElement(row.id)">恢复</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-empty v-else-if="!elementError" description="暂无元素" />
            </div>
            <el-pagination
              v-model:current-page="elementPage"
              v-model:page-size="elementPageSize"
              :total="elementTotal"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              class="pager"
              @current-change="onElementPageChange"
              @size-change="onElementPageSizeChange"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="变量覆盖" name="variable">
          <div class="tab-panel">
            <el-alert v-if="variableError" type="error" :closable="false" show-icon class="load-error">
              <template #title>{{ variableError }}</template>
              <el-button size="small" type="danger" plain @click="loadVariables()">重试</el-button>
            </el-alert>
            <div class="table-shell">
              <el-empty v-if="variableLoading && !variables.length" description="加载中..." />
              <el-table v-else-if="variables.length" :data="variables" v-loading="variableLoading" size="small" height="100%">
                <el-table-column prop="name" label="变量" min-width="120" />
                <el-table-column prop="scope" label="来源" width="80" />
                <el-table-column label="公共值" min-width="150" show-overflow-tooltip>
                  <template #default="{ row }">{{ variableSourceSummary(row as Variable) }}</template>
                </el-table-column>
                <el-table-column label="覆盖值" min-width="230">
                  <template #default="{ row }"><el-input v-model="variableDrafts[row.name].value" size="small" placeholder="可保存为空字符串；恢复则继承公共值" /></template>
                </el-table-column>
                <el-table-column label="操作" width="125" align="right">
                  <template #default="{ row }">
                    <el-button size="small" type="primary" text :loading="savingKey === `variable:${row.name}`" @click="saveVariable(row.name)">保存</el-button>
                    <el-button v-if="overriddenVariables.has(row.name)" size="small" text @click="restoreVariable(row.name)">恢复</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-empty v-else-if="!variableError" description="暂无变量" />
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="configDialogVisible" title="编辑智能定位配置" width="560px">
      <SmartLocatorEditor v-model="configDraft" />
      <template #footer>
        <el-button @click="configDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveConfigDialog">保存</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<style scoped>
.override-content {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}
.override-tabs {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  margin-top: 12px;
}
.table-toolbar {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.element-search { max-width: 280px; }
.load-error { flex-shrink: 0; margin-bottom: 8px; }
.tab-panel {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
}
.table-shell {
  flex: 1;
  min-height: 120px;
  overflow: hidden;
}
.pager { flex-shrink: 0; justify-content: flex-end; padding-top: 12px; }
.smart-tag {
  display: inline-block;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  margin-right: 4px;
}
:deep(.profile-override-drawer .el-drawer__body) {
  display: flex;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}
:deep(.override-tabs .el-tabs__content) { flex: 1; min-height: 0; }
:deep(.override-tabs .el-tab-pane) { height: 100%; }
@media (max-width: 720px) {
  .element-search { max-width: none; flex: 1; }
  .table-toolbar { flex-wrap: wrap; }
  .table-shell { min-height: 180px; }
  :deep(.profile-override-drawer) { width: calc(100vw - 16px) !important; }
}
</style>
