<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import AppProfileTree from '@/components/AppProfileTree.vue'
import DevicePicker from '@/components/DevicePicker.vue'
import ProfileStatusTag from '@/components/ProfileStatusTag.vue'
import ProfileReleaseManager from '@/components/ProfileReleaseManager.vue'
import ProfileDifferenceView from '@/components/ProfileDifferenceView.vue'
import ProfileOverrideDrawer from '@/components/ProfileOverrideDrawer.vue'
import {
  getAppProfile,
  listReleases,
  listProfileOverrides,
  patchProfileSuiteCaseVariables,
  restoreNodeOverride,
  restoreSuiteStepOverride,
  skipRulesBatch,
  upsertNodeOverride,
  upsertSuiteStepOverride,
  type ProfileCaseVariable,
  type ProfileNode,
  type ProfileVariableUpdate,
  type SkipTarget,
} from '@/api/appProfiles'
import { usePermission } from '@/composables/usePermission'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'
import { useAppProfileStore } from '@/stores/appProfile'
import { buildProfileSkipTarget } from '@/utils/appProfileSkip'
import {
  variableDisplayText,
  variableEditSeed,
  variableOverrideState,
  variableQuickUpdates,
  variableRestoreUpdates,
  variableStatusMeta,
  variableToken,
} from '@/utils/caseVariables'
import { stepVariableReferences } from '@/utils/variableReferences'

interface DisplayNode extends ProfileNode {
  _key: string
  _depth: number
  _suiteId?: number
  _caseId?: number
  /** 用例节点的编排项身份（重复编排时区分展开与覆盖） */
  _membershipId?: number
  _phase?: 'suite_setup' | 'suite_teardown'
  _isPhaseGroup?: boolean
}

const route = useRoute()
const router = useRouter()
const navigation = useWorkspaceNavigation()
const store = useAppProfileStore()
const { canEditProject, canExecute } = usePermission()
const projectId = computed(() => Number(route.params.projectId))

const releaseMgr = ref(false)
const diffView = ref(false)
const overrideDrawer = ref(false)
const selectedRows = ref<DisplayNode[]>([])
const saving = ref(false)
const devicePicker = ref<InstanceType<typeof DevicePicker> | null>(null)
const runAllLoading = ref(false)

const skipDialog = reactive({
  visible: false,
  reasonCode: 'unsupported',
  note: '',
  targets: [] as SkipTarget[],
})

const variableOverrideDialog = reactive({
  visible: false,
  row: null as DisplayNode | null,
  names: [] as string[],
  values: {} as Record<string, string>,
  enabled: {} as Record<string, boolean>,
  existingPatch: {} as Record<string, unknown>,
})

function variableNames(row: DisplayNode): string[] {
  return stepVariableReferences(row)
}

function canVariableOverride(row: DisplayNode): boolean {
  return (row.node_type === 'step' || row.node_type === 'suite_step') && variableNames(row).length > 0
}

function elementDisplayName(row: DisplayNode): string | null {
  if ((row.node_type !== 'step' && row.node_type !== 'suite_step') || row.element_id == null) return null
  return row.element_name ?? `未知元素（#${row.element_id}）`
}

const displayRows = computed<DisplayNode[]>(() => {
  const rows: DisplayNode[] = []
  for (const suite of store.suitePage?.items ?? []) {
    if (suite.id == null) continue
    const suiteId = suite.id
    rows.push({ ...suite, _key: `suite:${suiteId}`, _depth: 0, _suiteId: suiteId })
    if (!store.expandedKeys.has(`suite:${suiteId}`)) continue

    // 套件前后置伪节点行（depth 1）：展开后懒加载对应步骤节点
    const phaseGroups: { key: string; phase: 'suite_setup' | 'suite_teardown'; label: string; count: number }[] = [
      { key: `suite:${suiteId}:setup`, phase: 'suite_setup', label: '套件前置', count: suite.setup_step_count ?? 0 },
      { key: `suite:${suiteId}:teardown`, phase: 'suite_teardown', label: '套件后置', count: suite.teardown_step_count ?? 0 },
    ]
    for (const g of phaseGroups) {
      const expandedGroup = store.expandedKeys.has(g.key)
      rows.push({
        node_type: 'suite_step',
        id: null,
        node_key: null,
        name: `${g.label} (${g.count})`,
        phase: g.phase,
        effective_status: 'enabled',
        status_source: 'none',
        reason: null,
        override_count: 0,
        has_children: g.count > 0,
        _key: g.key,
        _depth: 1,
        _suiteId: suiteId,
        _phase: g.phase,
        _isPhaseGroup: true,
      })
      if (!expandedGroup) continue
      for (const node of store.childrenByParent[g.key] ?? []) {
        const nodeKey = node.node_key ?? String(node.id ?? '')
        rows.push({
          ...node,
          _key: `${node.node_type}:${suiteId}:${g.phase}:${nodeKey}`,
          _depth: 2,
          _suiteId: suiteId,
          _phase: g.phase,
        })
      }
    }

    for (const testCase of store.childrenByParent[`suite:${suiteId}`] ?? []) {
      if (testCase.id == null) continue
      const caseId = testCase.id
      // 用例行键用 suite_case_id：同一用例重复编排时各自独立展开与覆盖
      const membershipId = testCase.suite_case_id ?? caseId
      const caseKey = `case:${suiteId}:${membershipId}`
      rows.push({ ...testCase, _key: caseKey, _depth: 1, _suiteId: suiteId, _caseId: caseId, _membershipId: membershipId })
      if (!store.expandedKeys.has(caseKey)) continue
      for (const node of store.childrenByParent[caseKey] ?? []) {
        const nodeKey = node.node_key ?? String(node.id)
        rows.push({
          ...node,
          _key: `${node.node_type}:${suiteId}:${membershipId}:${nodeKey}`,
          _depth: 2,
          _suiteId: suiteId,
          _caseId: caseId,
          _membershipId: membershipId,
        })
      }
    }
  }
  return rows
})

function displayNode(row: unknown): DisplayNode {
  return row as DisplayNode
}

async function load() {
  store.projectId = projectId.value
  await store.loadProfiles()
  if (store.selectedProfileId != null) await store.selectProfile(store.selectedProfileId)
}

async function toggleNode(row: DisplayNode) {
  if (row._isPhaseGroup) {
    if (row.has_children && row._suiteId != null && row._phase != null && !store.expandedKeys.has(row._key)) {
      await store.loadSuiteSteps(row._suiteId, row._phase)
    }
    store.toggleExpand(row._key)
    return
  }
  if (!row.has_children || row.id == null) return
  if (row.node_type === 'suite') {
    if (!store.expandedKeys.has(row._key)) await store.loadChildren('suite', row.id)
  } else if (row.node_type === 'case' && row._suiteId != null) {
    if (!store.expandedKeys.has(row._key)) {
      await store.loadChildren('case', row.id, row._suiteId, row._membershipId)
    }
  }
  store.toggleExpand(row._key)
}

function targetFor(node: DisplayNode): SkipTarget | null {
  return buildProfileSkipTarget(node)
}

function openSkip(nodes: DisplayNode[]) {
  skipDialog.targets = nodes.map(targetFor).filter((target): target is SkipTarget => target != null)
  if (skipDialog.targets.length > 0) skipDialog.visible = true
}

async function mutateSkip(operation: 'skip' | 'restore', targets: SkipTarget[]) {
  if (!store.selectedProfileId || store.profileRevision == null || targets.length === 0) return
  saving.value = true
  try {
    const res = await skipRulesBatch(store.selectedProfileId, {
      expected_revision: store.profileRevision,
      operation,
      reason: operation === 'skip'
        ? { code: skipDialog.reasonCode, note: skipDialog.note || undefined }
        : undefined,
      targets,
    })
    store.markRevision(res.revision_after, store.testAssetRevision ?? 1)
    ElMessage.success(operation === 'skip' ? `已跳过 ${res.changed} 项` : `已恢复 ${res.changed} 项`)
    selectedRows.value = []
    await store.refreshVisibleWorkspace()
    // 左侧树 skip_counts（“X 用例 / Y 步骤”）随跳过/恢复实时变化，需重新拉取档案列表
    await store.loadProfiles()
  } finally {
    saving.value = false
  }
}

async function confirmSkip() {
  if (skipDialog.reasonCode === 'other' && !skipDialog.note.trim()) {
    ElMessage.warning('选择“其他”原因时必须填写备注')
    return
  }
  await mutateSkip('skip', skipDialog.targets)
  skipDialog.visible = false
  skipDialog.note = ''
}

async function restoreRow(row: DisplayNode) {
  const target = targetFor(row)
  if (target) await mutateSkip('restore', [target])
}

async function batchRestore() {
  const targets = selectedRows.value
    .filter((row) => row.status_source === 'direct')
    .map(targetFor)
    .filter((target): target is SkipTarget => target != null)
  await mutateSkip('restore', targets)
}

async function openVariableOverride(row: DisplayNode) {
  if (!store.selectedProfileId || !row.node_key || row._suiteId == null) return
  if (!canVariableOverride(row) || (row.node_type !== 'suite_step' && row._caseId == null)) return
  const data = await listProfileOverrides(store.selectedProfileId)
  const existing = data.nodes.find((item) => (
    item.suite_id === row._suiteId
      && (item.suite_case_id ?? null) === (row._membershipId ?? null)
      && item.case_id === (row.node_type === 'suite_step' ? null : row._caseId)
      && item.node_type === row.node_type
      && item.node_key === row.node_key
  ))
  const names = variableNames(row)
  const existingVariables = existing?.patch?.variable_overrides
  const values: Record<string, string> = {}
  const enabled: Record<string, boolean> = {}
  for (const name of names) {
    const hasValue = existingVariables != null
      && typeof existingVariables === 'object'
      && Object.prototype.hasOwnProperty.call(existingVariables, name)
    values[name] = hasValue ? String((existingVariables as Record<string, unknown>)[name]) : ''
    enabled[name] = hasValue
  }
  variableOverrideDialog.row = row
  variableOverrideDialog.names = names
  variableOverrideDialog.values = values
  variableOverrideDialog.enabled = enabled
  variableOverrideDialog.existingPatch = { ...(existing?.patch ?? {}) }
  variableOverrideDialog.visible = true
}

// ---------- 用例变量就地快捷覆盖 ----------
// 用例行竖排展示全部变量；点击单个变量就地编辑，一个值写入它在当前用例的全部引用节点。
const caseVariableEdit = reactive({ key: '', value: '' })
const variableSaving = ref(false)

function caseVariables(row: DisplayNode): ProfileCaseVariable[] {
  return row.variables ?? []
}

function caseVariableKey(row: DisplayNode, variable: ProfileCaseVariable): string {
  return `${row._key}:${variable.name}`
}

function isEditingVariable(row: DisplayNode, variable: ProfileCaseVariable): boolean {
  return caseVariableEdit.key === caseVariableKey(row, variable)
}

function beginVariableEdit(row: DisplayNode, variable: ProfileCaseVariable) {
  if (!canEditProject.value) return
  caseVariableEdit.key = caseVariableKey(row, variable)
  caseVariableEdit.value = variableEditSeed(variable)
}

function cancelVariableEdit() {
  caseVariableEdit.key = ''
  caseVariableEdit.value = ''
}

/** 保存后只就地替换当前编排项的变量列表，保持展开状态与滚动位置。 */
function applyCaseVariables(suiteId: number, membershipId: number, variables: ProfileCaseVariable[]) {
  const key = `suite:${suiteId}`
  const rows = store.childrenByParent[key]
  if (!rows) return
  store.childrenByParent = {
    ...store.childrenByParent,
    [key]: rows.map((node) => node.suite_case_id === membershipId
      ? { ...node, variable_count: variables.length, variables }
      : node),
  }
}

async function refreshCaseChildren(row: DisplayNode) {
  if (row._caseId != null && row._suiteId != null) {
    await store.loadChildren('case', row._caseId, row._suiteId, row._membershipId, true)
  } else {
    await store.refreshVisibleWorkspace()
  }
}

async function saveCaseVariableUpdates(row: DisplayNode, updates: ProfileVariableUpdate[]) {
  const membershipId = row._membershipId
  if (!store.selectedProfileId || store.profileRevision == null || membershipId == null) return
  if (updates.length === 0) {
    ElMessage.info('没有需要保存的变更')
    return
  }
  variableSaving.value = true
  try {
    const result = await patchProfileSuiteCaseVariables(store.selectedProfileId, membershipId, {
      expected_revision: store.profileRevision,
      updates,
    })
    store.markRevision(result.revision, store.testAssetRevision ?? 1)
    if (row._suiteId != null) applyCaseVariables(row._suiteId, membershipId, result.variables)
    cancelVariableEdit()
    ElMessage.success('变量覆盖已保存')
  } catch (error) {
    ElMessage.error((error as Error).message || '保存失败，请刷新后重试')
  } finally {
    variableSaving.value = false
  }
}

async function commitVariableEdit(row: DisplayNode, variable: ProfileCaseVariable) {
  const updates = variableQuickUpdates(variable, caseVariableEdit.value)
  if (updates == null) {
    cancelVariableEdit()
    ElMessage.info('没有需要保存的变更')
    return
  }
  await saveCaseVariableUpdates(row, updates)
}

/** 恢复原值：删除该变量在当前用例全部引用节点上的覆盖。 */
async function restoreCaseVariable(row: DisplayNode, variable: ProfileCaseVariable) {
  await saveCaseVariableUpdates(row, variableRestoreUpdates(variable))
}

/** 步骤行「变量覆盖」入口与用例行就地覆盖共用同一批量接口。 */
function buildStepUpdates(row: DisplayNode, value: (name: string) => string | null): ProfileVariableUpdate[] {
  return variableOverrideDialog.names.map((name) => ({
    node_type: 'step',
    node_key: row.node_key as string,
    name,
    value: value(name),
  }))
}

async function saveVariableOverride() {
  const row = variableOverrideDialog.row
  if (!store.selectedProfileId || store.profileRevision == null || !row?.node_key) return
  const membershipId = row._membershipId
  if (row.node_type === 'step' && membershipId != null) {
    saving.value = true
    try {
      const result = await patchProfileSuiteCaseVariables(store.selectedProfileId, membershipId, {
        expected_revision: store.profileRevision,
        updates: buildStepUpdates(row, (name) => (
          variableOverrideDialog.enabled[name] ? (variableOverrideDialog.values[name] ?? '') : null
        )),
      })
      store.markRevision(result.revision, store.testAssetRevision ?? 1)
      if (row._suiteId != null) applyCaseVariables(row._suiteId, membershipId, result.variables)
      variableOverrideDialog.visible = false
      await refreshCaseChildren(row)
      ElMessage.success('节点覆盖已保存')
    } catch (error) {
      ElMessage.error((error as Error).message || '保存失败，请刷新后重试')
    } finally {
      saving.value = false
    }
    return
  }
  const variableOverrides: Record<string, string> = {}
  for (const name of variableOverrideDialog.names) {
    if (variableOverrideDialog.enabled[name]) variableOverrides[name] = variableOverrideDialog.values[name] ?? ''
  }
  const patch = { ...variableOverrideDialog.existingPatch }
  if (Object.keys(variableOverrides).length > 0) patch.variable_overrides = variableOverrides
  else delete patch.variable_overrides
  if (Object.keys(patch).length === 0) {
    await restoreVariableOverride()
    return
  }
  saving.value = true
  try {
    const result = row.node_type === 'suite_step'
      ? await upsertSuiteStepOverride(
          store.selectedProfileId,
          row._suiteId!,
          row.node_key,
          { expected_revision: store.profileRevision, patch },
        )
      : await upsertNodeOverride(
          store.selectedProfileId,
          row._suiteId!,
          row._caseId!,
          'step',
          row.node_key,
          { expected_revision: store.profileRevision, patch },
        )
    store.markRevision(result.revision, store.testAssetRevision ?? 1)
    variableOverrideDialog.visible = false
    await store.refreshVisibleWorkspace()
    ElMessage.success('节点覆盖已保存')
  } finally {
    saving.value = false
  }
}

async function restoreVariableOverride() {
  const row = variableOverrideDialog.row
  if (!store.selectedProfileId || store.profileRevision == null || !row?.node_key) return
  const membershipId = row._membershipId
  if (row.node_type === 'step' && membershipId != null) {
    saving.value = true
    try {
      const result = await patchProfileSuiteCaseVariables(store.selectedProfileId, membershipId, {
        expected_revision: store.profileRevision,
        updates: buildStepUpdates(row, () => null),
      })
      store.markRevision(result.revision, store.testAssetRevision ?? 1)
      if (row._suiteId != null) applyCaseVariables(row._suiteId, membershipId, result.variables)
      variableOverrideDialog.visible = false
      await refreshCaseChildren(row)
      ElMessage.success('节点覆盖已恢复')
    } catch (error) {
      ElMessage.error((error as Error).message || '恢复失败，请刷新后重试')
    } finally {
      saving.value = false
    }
    return
  }
  const rest = { ...variableOverrideDialog.existingPatch }
  delete rest.variable_overrides
  if (Object.keys(rest).length > 0) {
    saving.value = true
    try {
      const result = row.node_type === 'suite_step'
        ? await upsertSuiteStepOverride(store.selectedProfileId, row._suiteId!, row.node_key, {
            expected_revision: store.profileRevision, patch: rest,
          })
        : await upsertNodeOverride(store.selectedProfileId, row._suiteId!, row._caseId!, 'step', row.node_key, {
            expected_revision: store.profileRevision, patch: rest,
          })
      store.markRevision(result.revision, store.testAssetRevision ?? 1)
    } finally {
      saving.value = false
    }
  } else {
    if (row.node_type === 'suite_step') {
      await restoreSuiteStepOverride(store.selectedProfileId, row._suiteId!, row.node_key, { expected_revision: store.profileRevision })
    } else {
      await restoreNodeOverride(store.selectedProfileId, row._suiteId!, row._caseId!, 'step', row.node_key, { expected_revision: store.profileRevision })
    }
  }
  const profile = await getAppProfile(store.selectedProfileId)
  store.markRevision(profile.revision, store.testAssetRevision ?? 1)
  variableOverrideDialog.visible = false
  await store.refreshVisibleWorkspace()
  ElMessage.success('节点覆盖已恢复')
}

async function refreshProfile() {
  if (store.selectedProfileId) await store.refreshVisibleWorkspace()
}

async function runNode(row: DisplayNode) {
  if (row.id == null || (row.node_type !== 'suite' && row.node_type !== 'case')) return
  await runTarget(row.node_type, row.id, row.name)
}

async function runTarget(kind: 'suite' | 'case', id: number, name: string) {
  if (!store.selectedProfileId || store.profileRevision == null || store.testAssetRevision == null) return
  const page = await listReleases(store.selectedProfileId, { status: 'active', page_size: 100 })
  const release = page.items[0]
  if (!release) {
    ElMessage.warning('当前档案没有可用的发布版本，请先创建发布版本')
    return
  }
  const execution = await devicePicker.value?.open(
    { kind, id, name },
    {
      targetId: id,
      profile: {
        app_profile_id: store.selectedProfileId,
        app_release_id: release.id,
        app_release_version: release.version,
        expected_profile_revision: store.profileRevision,
        expected_test_asset_revision: store.testAssetRevision,
      },
    },
  )
  if (execution) await router.push(navigation.executionDetail(execution.id))
}

async function runAllSuites() {
  if (!store.selectedProfileId || store.profileRevision == null || store.testAssetRevision == null) return
  runAllLoading.value = true
  try {
    const page = await listReleases(store.selectedProfileId, { status: 'active', page_size: 100 })
    const release = page.items[0]
    if (!release) {
      ElMessage.warning('当前档案没有可用的发布版本，请先创建发布版本')
      return
    }
    const execution = await devicePicker.value?.open(
      {
        kind: 'batch',
        suiteIds: [],
        targetScope: 'profile_all',
        name: `${store.currentProfile?.name ?? '当前 APP'}全部套件`,
      },
      {
        profile: {
          app_profile_id: store.selectedProfileId,
          app_release_id: release.id,
          app_release_version: release.version,
          expected_profile_revision: store.profileRevision,
          expected_test_asset_revision: store.testAssetRevision,
        },
      },
    )
    if (execution) await router.push(navigation.executionDetail(execution.id))
  } finally {
    runAllLoading.value = false
  }
}

async function updateRevision(revision: number) {
  store.markRevision(revision, store.testAssetRevision ?? 1)
  await store.refreshVisibleWorkspace()
}

watch(() => route.params.projectId, load)
onMounted(load)
</script>

<template>
  <div class="profile-workspace">
    <aside class="tree-panel"><AppProfileTree :project-id="projectId" /></aside>
    <section class="workspace-panel">
      <div class="workspace-head">
        <div class="head-left">
          <span class="profile-name">{{ store.currentProfile?.name ?? '未选档案' }}</span>
          <span class="rev">revision {{ store.profileRevision ?? '-' }}</span>
          <el-button size="small" text @click="refreshProfile">刷新</el-button>
        </div>
        <div class="head-right">
          <el-button v-if="canExecute" size="small" type="primary" :loading="runAllLoading" :disabled="!store.selectedProfileId" @click="runAllSuites">运行当前 APP 全部套件</el-button>
          <el-button v-if="canEditProject" size="small" @click="releaseMgr = true">发布版本</el-button>
          <el-button size="small" @click="diffView = true">差异清单</el-button>
          <el-button v-if="canEditProject" size="small" @click="overrideDrawer = true">覆盖配置</el-button>
          <el-input v-model="store.filters.keyword" size="small" placeholder="搜索套件" clearable style="width: 180px" @change="store.loadWorkspace" />
          <el-select v-model="store.filters.effective_status" size="small" style="width: 130px" @change="store.loadWorkspace">
            <el-option label="全部状态" value="all" />
            <el-option label="正常" value="enabled" />
            <el-option label="已跳过" value="skipped" />
            <el-option label="已覆盖" value="overridden" />
          </el-select>
        </div>
      </div>

      <div v-if="canEditProject && selectedRows.length" class="batch-bar">
        <span>已选 {{ selectedRows.length }} 项</span>
        <el-button size="small" type="danger" plain @click="openSkip(selectedRows)">批量跳过</el-button>
        <el-button size="small" @click="batchRestore">批量恢复直接规则</el-button>
      </div>

      <el-table v-loading="store.loading || saving" :data="displayRows" row-key="_key" size="small" @selection-change="selectedRows = $event">
        <el-table-column v-if="canEditProject" type="selection" width="42" :selectable="(row: unknown) => !displayNode(row)._isPhaseGroup" />
        <el-table-column label="名称" min-width="260">
          <template #default="{ row }">
            <span class="node-name" :style="{ paddingLeft: `${row._depth * 22}px` }">
              <button v-if="row.has_children" type="button" class="expand-button" @click="toggleNode(displayNode(row))">{{ store.expandedKeys.has(row._key) ? '▾' : '▸' }}</button>
              <span v-else class="node-dot">·</span>
              <span class="node-label"><span>{{ row.name }}</span><small v-if="elementDisplayName(displayNode(row))" class="element-label">元素：{{ elementDisplayName(displayNode(row)) }}</small></span>
            </span>
            <div
              v-if="row.node_type === 'case' && caseVariables(displayNode(row)).length"
              class="case-variable-list"
              :style="{ paddingLeft: `${row._depth * 22 + 22}px` }"
              @click.stop
              @dblclick.stop
              @mousedown.stop
            >
              <div
                v-for="variable in caseVariables(displayNode(row))"
                :key="variable.name"
                class="case-variable-item"
                :class="`tone-${variableStatusMeta(variable.status).tone}`"
              >
                <code class="variable-name">{{ variableToken(variable.name) }}</code>
                <template v-if="isEditingVariable(displayNode(row), variable)">
                  <el-input
                    v-model="caseVariableEdit.value"
                    class="variable-input"
                    size="small"
                    :disabled="variableSaving"
                    placeholder="输入覆盖值（可为空）"
                    :aria-label="`${variable.name} 的覆盖值`"
                    @click.stop
                    @keyup.enter="commitVariableEdit(displayNode(row), variable)"
                    @keyup.esc="cancelVariableEdit"
                  />
                  <el-button size="small" type="primary" :loading="variableSaving" @click.stop="commitVariableEdit(displayNode(row), variable)">保存</el-button>
                  <el-button size="small" :disabled="variableSaving" @click.stop="cancelVariableEdit">取消</el-button>
                  <el-button
                    v-if="variableOverrideState(variable).overridden"
                    size="small"
                    text
                    type="danger"
                    :disabled="variableSaving"
                    @click.stop="restoreCaseVariable(displayNode(row), variable)"
                  >恢复原值</el-button>
                </template>
                <template v-else>
                  <span
                    class="variable-value"
                    :class="{ editable: canEditProject }"
                    :title="`${variableToken(variable.name)} = ${variableDisplayText(variable)}（引用 ${variable.reference_count} 处）`"
                    @click.stop="beginVariableEdit(displayNode(row), variable)"
                  >{{ variableDisplayText(variable) }}</span>
                  <span class="variable-refs" :title="`共 ${variable.reference_count} 处引用`">×{{ variable.reference_count }}</span>
                </template>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90" prop="node_type" />
        <el-table-column label="阶段" width="90"><template #default="{ row }">{{ row.phase ?? '-' }}</template></el-table-column>
        <el-table-column label="生效状态" width="120">
          <template #default="{ row }"><ProfileStatusTag :effective-status="row.effective_status" :status-source="row.status_source" /></template>
        </el-table-column>
        <el-table-column label="原因" min-width="150"><template #default="{ row }">{{ row.reason?.note || row.reason?.code || '-' }}</template></el-table-column>
        <el-table-column label="操作" width="210" align="right">
          <template #default="{ row }">
            <el-button v-if="canExecute && (row.node_type === 'suite' || row.node_type === 'case')" size="small" text type="primary" @click="runNode(displayNode(row))">运行</el-button>
            <template v-if="canEditProject && !displayNode(row)._isPhaseGroup">
              <el-button v-if="canVariableOverride(displayNode(row))" size="small" text @click="openVariableOverride(displayNode(row))">变量覆盖</el-button>
              <el-button v-if="row.status_source === 'direct'" size="small" text @click="restoreRow(displayNode(row))">恢复</el-button>
              <el-button v-else-if="row.status_source !== 'inherited'" size="small" text type="danger" @click="openSkip([displayNode(row)])">跳过</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!store.loading && displayRows.length === 0" description="暂无套件" />

      <el-dialog v-model="skipDialog.visible" title="设置跳过规则" width="420px">
        <el-form label-width="80px">
          <el-form-item label="影响范围">{{ skipDialog.targets.length }} 项</el-form-item>
          <el-form-item label="原因">
            <el-select v-model="skipDialog.reasonCode" style="width: 100%">
              <el-option label="不支持" value="unsupported" /><el-option label="未适配" value="not_adapted" />
              <el-option label="已废弃" value="deprecated" /><el-option label="环境限制" value="environment_limit" />
              <el-option label="其他" value="other" />
            </el-select>
          </el-form-item>
          <el-form-item label="备注"><el-input v-model="skipDialog.note" type="textarea" :rows="2" placeholder="可选，“其他”必填" /></el-form-item>
        </el-form>
        <template #footer><el-button @click="skipDialog.visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="confirmSkip">确认跳过</el-button></template>
      </el-dialog>

      <el-dialog v-model="variableOverrideDialog.visible" width="min(640px, calc(100vw - 32px))" class="variable-override-dialog">
        <template #header>
          <div class="override-dialog-header">
            <div class="override-dialog-title">步骤变量覆盖</div>
            <div class="override-dialog-subtitle">仅修改当前步骤引用的变量</div>
          </div>
        </template>
        <div v-if="variableOverrideDialog.row" class="override-context-card">
          <div class="context-main">
            <span class="context-kicker">当前步骤</span>
            <strong class="context-name" :title="variableOverrideDialog.row.name">{{ variableOverrideDialog.row.name }}</strong>
            <span class="context-action">{{ variableOverrideDialog.row.registry_key || '动作' }}</span>
          </div>
          <div class="context-details">
            <span v-if="variableOverrideDialog.row.order != null">第 {{ variableOverrideDialog.row.order }} 项</span>
            <span v-if="elementDisplayName(variableOverrideDialog.row)" class="element-context" :title="elementDisplayName(variableOverrideDialog.row) ?? undefined">元素：{{ elementDisplayName(variableOverrideDialog.row) }}</span>
          </div>
        </div>
        <div class="override-hint" role="note">
          覆盖值仅对当前步骤生效；未启用的变量继续继承原值，执行参数优先级仍高于此处。
        </div>
        <section class="variable-section" aria-labelledby="variable-section-title">
          <div class="variable-section-head">
            <span id="variable-section-title" class="variable-section-title">变量覆盖</span>
            <span class="variable-section-count">已启用 {{ Object.values(variableOverrideDialog.enabled).filter(Boolean).length }} / 共 {{ variableOverrideDialog.names.length }}</span>
          </div>
          <div class="variable-list" role="list">
            <div v-for="name in variableOverrideDialog.names" :key="name" class="variable-row" role="listitem">
              <div class="variable-meta">
                <el-tooltip :content="variableToken(name)" placement="top">
                  <code class="variable-name">{{ variableToken(name) }}</code>
                </el-tooltip>
                <span class="variable-state">{{ variableOverrideDialog.enabled[name] ? '已启用覆盖' : '继承原值' }}</span>
              </div>
              <el-input
                v-model="variableOverrideDialog.values[name]"
                class="variable-input"
                :disabled="!variableOverrideDialog.enabled[name]"
                :placeholder="variableOverrideDialog.enabled[name] ? '输入当前步骤的覆盖值（可为空）' : '启用覆盖后输入值'"
                :aria-label="`${name} 的步骤覆盖值`"
              />
              <el-switch v-model="variableOverrideDialog.enabled[name]" :aria-label="`启用 ${name} 覆盖`" />
            </div>
          </div>
        </section>
        <template #footer>
          <div class="override-dialog-footer">
            <div class="footer-left">
              <el-button v-if="Object.prototype.hasOwnProperty.call(variableOverrideDialog.existingPatch, 'variable_overrides')" text type="danger" @click="restoreVariableOverride">恢复全部变量</el-button>
            </div>
            <div class="footer-right">
              <el-button @click="variableOverrideDialog.visible = false">取消</el-button>
              <el-button type="primary" :loading="saving" @click="saveVariableOverride">保存覆盖</el-button>
            </div>
          </div>
        </template>
      </el-dialog>

      <template v-if="store.selectedProfileId">
        <ProfileReleaseManager v-if="releaseMgr" v-model="releaseMgr" :profile-id="store.selectedProfileId" :revision="store.profileRevision ?? 1" />
        <ProfileDifferenceView v-model="diffView" :profile-id="store.selectedProfileId" />
        <ProfileOverrideDrawer v-model="overrideDrawer" :profile-id="store.selectedProfileId" :project-id="projectId" :revision="store.profileRevision ?? 1" @revision-change="updateRevision" />
      </template>
      <DevicePicker ref="devicePicker" :project-id="projectId" />
    </section>
  </div>
</template>

<style scoped>
.profile-workspace { display: flex; gap: 16px; height: calc(100vh - 120px); }
.tree-panel { width: 240px; flex-shrink: 0; border-right: 1px solid var(--el-border-color-light); overflow: auto; }
.workspace-panel { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.workspace-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.head-left, .head-right, .batch-bar { display: flex; align-items: center; gap: 8px; }
.head-right { flex-wrap: wrap; justify-content: flex-end; }
.profile-name { font-weight: 600; font-size: 16px; }
.rev { color: var(--el-text-color-secondary); font-size: 12px; }
.batch-bar { padding: 8px 12px; border-radius: 6px; background: var(--el-color-primary-light-9); }
.node-name { display: inline-flex; align-items: center; }
.node-label { display: inline-flex; flex-direction: column; gap: 2px; }
/* 用例行变量：竖排展示全部变量，点击就地编辑（一个值写入全部引用节点） */
.case-variable-list { display: flex; flex-direction: column; gap: 3px; margin-top: 4px; }
.case-variable-item {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 22px;
  padding: 0 4px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
}
.case-variable-item .variable-name { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.case-variable-item .variable-value {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.case-variable-item .variable-value.editable { cursor: pointer; border-bottom: 1px dashed transparent; }
.case-variable-item .variable-value.editable:hover { border-bottom-color: currentColor; }
.case-variable-item .variable-refs { flex-shrink: 0; opacity: 0.6; }
.case-variable-item .variable-input { width: 200px; }
.case-variable-item.tone-overridden { color: #1d4ed8; background: rgba(37, 99, 235, 0.12); border-color: rgba(37, 99, 235, 0.35); }
.case-variable-item.tone-inherited { color: var(--el-text-color-regular); background: rgba(100, 116, 139, 0.08); border-color: rgba(100, 116, 139, 0.24); }
.case-variable-item.tone-undefined { color: #c2410c; background: rgba(249, 115, 22, 0.12); border-color: rgba(249, 115, 22, 0.35); }
.case-variable-item.tone-random { color: #7c3aed; background: rgba(139, 92, 246, 0.14); border-color: rgba(139, 92, 246, 0.38); }
.case-variable-item.tone-mixed { color: #b45309; background: rgba(245, 158, 11, 0.14); border-color: rgba(245, 158, 11, 0.4); }
.element-label, .element-context { color: var(--el-text-color-secondary); font-size: 12px; }
.expand-button { width: 22px; padding: 0; border: 0; background: transparent; cursor: pointer; color: inherit; }
.node-dot { display: inline-block; width: 22px; text-align: center; }
.override-dialog-header { display: flex; flex-direction: column; gap: 2px; }
.override-dialog-title { color: var(--el-text-color-primary); font-size: 17px; font-weight: 600; line-height: 1.35; }
.override-dialog-subtitle { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }
.override-context-card { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 14px; border: 1px solid var(--el-border-color-light); border-radius: 8px; background: var(--el-color-primary-light-9); }
.context-main, .context-details { display: flex; align-items: center; min-width: 0; gap: 8px; }
.context-main { flex: 1; flex-wrap: wrap; }
.context-kicker { color: var(--el-text-color-secondary); font-size: 12px; }
.context-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.context-action, .context-details { color: var(--el-text-color-secondary); font-size: 12px; }
.context-details { flex-shrink: 0; flex-wrap: wrap; justify-content: flex-end; }
.element-context { overflow: hidden; max-width: 240px; text-overflow: ellipsis; white-space: nowrap; }
.override-hint { margin-top: 12px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.variable-section { margin-top: 18px; }
.variable-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.variable-section-title { color: var(--el-text-color-primary); font-size: 14px; font-weight: 600; }
.variable-section-count { color: var(--el-text-color-secondary); font-size: 12px; }
.variable-list { display: flex; flex-direction: column; gap: 8px; max-height: min(46vh, 380px); overflow-y: auto; padding: 2px; }
.variable-row { display: flex; align-items: center; gap: 12px; min-width: 0; padding: 9px 10px; border: 1px solid var(--el-border-color-light); border-radius: 7px; background: var(--el-bg-color); }
.variable-meta { display: flex; flex-direction: column; flex: 0 0 150px; min-width: 0; gap: 3px; }
.variable-name { display: block; overflow: hidden; padding: 2px 6px; border-radius: 4px; background: var(--el-fill-color-light); color: var(--el-color-primary); text-overflow: ellipsis; white-space: nowrap; }
.variable-state { overflow: hidden; color: var(--el-text-color-secondary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.variable-input { flex: 1; min-width: 120px; }
.override-dialog-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; }
.footer-left, .footer-right { display: flex; align-items: center; gap: 8px; }
@media (max-width: 600px) {
  .override-context-card { align-items: flex-start; flex-direction: column; gap: 8px; }
  .context-details { justify-content: flex-start; }
  .variable-row { align-items: stretch; flex-wrap: wrap; }
  .variable-meta { flex-basis: calc(100% - 40px); }
  .variable-input { flex-basis: calc(100% - 40px); }
  .override-dialog-footer { align-items: stretch; flex-direction: column-reverse; }
  .footer-left, .footer-right { justify-content: flex-end; }
}
</style>
