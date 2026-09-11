<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Edit } from '@element-plus/icons-vue'

import AppProfileTree from '@/components/AppProfileTree.vue'
import DevicePicker from '@/components/DevicePicker.vue'
import ProfileStatusTag from '@/components/ProfileStatusTag.vue'
import ProfileReleaseManager from '@/components/ProfileReleaseManager.vue'
import ProfileDifferenceView from '@/components/ProfileDifferenceView.vue'
import ProfileOverrideDrawer from '@/components/ProfileOverrideDrawer.vue'
import {
  listReleases,
  patchProfileSuiteCaseVariables,
  skipRulesBatch,
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
const saving = ref(false)
const devicePicker = ref<InstanceType<typeof DevicePicker> | null>(null)
const runAllLoading = ref(false)

const skipDialog = reactive({
  visible: false,
  reasonCode: 'unsupported',
  note: '',
  targets: [] as SkipTarget[],
})

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
    for (const target of targets) {
      if (target.type !== 'suite' || target.suite_id == null) continue
      if (operation === 'skip') store.markSuiteSkipped(target.suite_id)
      else store.markSuiteRestored(target.suite_id)
    }
    ElMessage.success(operation === 'skip' ? `已跳过 ${res.changed} 项` : `已恢复 ${res.changed} 项`)
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

// ---------- 用例变量就地覆盖（仅 APP 档案用例行） ----------
// 「变量」列竖排展示 `变量名：变量值`；点右侧编辑图标进入编辑态，输入后由「保存」提交、「取消」放弃。
// 不做失焦自动保存（避免误触）。覆盖范围 = 该变量在**当前这条编排项**里的取值，
// 不写公共用例、不影响同一用例的其它编排。
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

async function saveCaseVariableUpdates(row: DisplayNode, updates: ProfileVariableUpdate[]) {
  const membershipId = row._membershipId
  const revision = store.profileRevision
  if (!store.selectedProfileId || membershipId == null) return
  if (updates.length === 0) return
  if (revision == null) {
    ElMessage.error('档案版本未就绪，请刷新后重试')
    return
  }
  variableSaving.value = true
  try {
    const result = await patchProfileSuiteCaseVariables(store.selectedProfileId, membershipId, {
      expected_revision: revision,
      updates,
    })
    // 版本号在 profile_revision；写成 result.revision 会拿到 undefined，
    // 之后所有「版本未就绪」守卫都会静默短路（保存/恢复双双失灵）。
    store.markRevision(result.profile_revision, store.testAssetRevision ?? 1)
    if (row._suiteId != null) applyCaseVariables(row._suiteId, membershipId, result.variables)
    cancelVariableEdit()
    ElMessage.success('变量已更新')
  } catch (error) {
    // 失败时保留编辑态，便于直接改完重试
    ElMessage.error((error as Error).message || '保存失败，请刷新后重试')
  } finally {
    variableSaving.value = false
  }
}

/** 「保存」提交（回车等价）；值没变化就静默收起编辑框（不发请求、不推进 revision）。 */
async function commitVariableEdit(row: DisplayNode, variable: ProfileCaseVariable) {
  if (variableSaving.value) return
  if (caseVariableEdit.key !== caseVariableKey(row, variable)) return
  const updates = variableQuickUpdates(variable, caseVariableEdit.value)
  if (updates == null) {
    cancelVariableEdit()
    return
  }
  await saveCaseVariableUpdates(row, updates)
}

/** 恢复原值：删除该变量在当前编排项上全部引用节点的覆盖，回到继承值。 */
async function restoreCaseVariable(row: DisplayNode, variable: ProfileCaseVariable) {
  if (variableSaving.value) return
  await saveCaseVariableUpdates(row, variableRestoreUpdates(variable))
}

async function refreshProfile() {
  if (store.selectedProfileId) await store.refreshVisibleWorkspace()
}

async function runNode(row: DisplayNode) {
  if (row.id == null || (row.node_type !== 'suite' && row.node_type !== 'case')) return
  if (row.effective_status === 'skipped') return
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
        excludedSuiteIds: [...store.excludedSuiteIds].sort((a, b) => a - b),
        name: `${store.currentProfile?.name ?? '当前 APP'}已选套件`,
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

function onSuiteSelectionChanged(row: DisplayNode, selected: boolean | string | number) {
  if (row.id == null || row.node_type !== 'suite') return
  store.setSuiteSelected(row.id, Boolean(selected), row.effective_status)
}

function onSuitePageChanged(page: number) {
  void store.loadWorkspace(page)
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
          <el-button v-if="canExecute" size="small" type="primary" :loading="runAllLoading" :disabled="!store.selectedProfileId || store.selectedSuiteCount === 0" @click="runAllSuites">运行已选套件</el-button>
          <span v-if="canExecute" class="selection-summary">已选 {{ store.selectedSuiteCount }} / {{ store.executionSelectableTotal }} 个套件</span>
          <el-button v-if="canExecute && store.excludedSuiteIds.size" size="small" text @click="store.restoreAllSuiteSelection">恢复全部选择</el-button>
          <el-button v-if="canEditProject" size="small" @click="releaseMgr = true">发布版本</el-button>
          <el-button size="small" @click="diffView = true">差异清单</el-button>
          <el-button v-if="canEditProject" size="small" @click="overrideDrawer = true">覆盖配置</el-button>
          <el-input v-model="store.filters.keyword" size="small" placeholder="搜索套件" clearable style="width: 180px" @change="() => store.loadWorkspace()" />
          <el-select v-model="store.filters.effective_status" size="small" style="width: 130px" @change="() => store.loadWorkspace()">
            <el-option label="全部状态" value="all" />
            <el-option label="正常" value="enabled" />
            <el-option label="已跳过" value="skipped" />
            <el-option label="已覆盖" value="overridden" />
          </el-select>
        </div>
      </div>

      <el-table v-loading="store.loading || saving" :data="displayRows" row-key="_key" size="small">
        <el-table-column v-if="canExecute" label="选择" width="58" align="center">
          <template #default="{ row }">
            <el-checkbox
              v-if="row.node_type === 'suite' && row.id != null"
              :model-value="store.isSuiteSelected(row.id, row.effective_status)"
              :disabled="row.effective_status === 'skipped'"
              :aria-label="`选择套件 ${row.name}`"
              @change="onSuiteSelectionChanged(displayNode(row), $event)"
            />
          </template>
        </el-table-column>
        <el-table-column label="名称" min-width="260">
          <template #default="{ row }">
            <span class="node-name" :style="{ paddingLeft: `${row._depth * 22}px` }">
              <button v-if="row.has_children" type="button" class="expand-button" @click="toggleNode(displayNode(row))">{{ store.expandedKeys.has(row._key) ? '▾' : '▸' }}</button>
              <span v-else class="node-dot">·</span>
              <span class="node-label"><span>{{ row.name }}</span><small v-if="elementDisplayName(displayNode(row))" class="element-label">元素：{{ elementDisplayName(displayNode(row)) }}</small></span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90" prop="node_type" />
        <el-table-column label="阶段" width="90"><template #default="{ row }">{{ row.phase ?? '-' }}</template></el-table-column>
        <el-table-column label="生效状态" width="120">
          <template #default="{ row }"><ProfileStatusTag :effective-status="row.effective_status" :status-source="row.status_source" /></template>
        </el-table-column>
        <el-table-column label="变量" min-width="260">
          <template #default="{ row }">
            <div
              v-if="row.node_type === 'case'"
              class="case-variable-list"
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
                <code class="variable-name" :title="`引用 ${variable.reference_count} 处`">{{ variableToken(variable.name) }}</code>
                <span class="variable-colon">：</span>
                <template v-if="isEditingVariable(displayNode(row), variable)">
                  <el-input
                    v-model="caseVariableEdit.value"
                    class="variable-input"
                    size="small"
                    autofocus
                    :disabled="variableSaving"
                    placeholder="输入新值"
                    :aria-label="`${variable.name} 的新值`"
                    @click.stop
                    @keyup.enter="commitVariableEdit(displayNode(row), variable)"
                    @keyup.esc="cancelVariableEdit"
                  />
                  <el-button size="small" type="primary" :loading="variableSaving" @click.stop="commitVariableEdit(displayNode(row), variable)">保存</el-button>
                  <el-button size="small" :disabled="variableSaving" @click.stop="cancelVariableEdit">取消</el-button>
                </template>
                <template v-else>
                  <span class="variable-value" :title="`引用 ${variable.reference_count} 处`">{{ variableDisplayText(variable) }}</span>
                  <el-button
                    v-if="canEditProject"
                    class="variable-icon-button"
                    size="small"
                    text
                    :icon="Edit"
                    :aria-label="`编辑 ${variable.name}`"
                    title="编辑该用例中的取值"
                    :disabled="variableSaving"
                    @click.stop="beginVariableEdit(displayNode(row), variable)"
                  />
                  <el-button
                    v-if="variableOverrideState(variable).overridden"
                    class="variable-restore"
                    size="small"
                    text
                    type="danger"
                    :disabled="variableSaving"
                    title="恢复原值"
                    @click.stop="restoreCaseVariable(displayNode(row), variable)"
                  >恢复</el-button>
                </template>
              </div>
              <span v-if="caseVariables(displayNode(row)).length === 0" class="case-variable-empty">无参数变量</span>
            </div>
            <span v-else class="case-variable-empty">-</span>
          </template>
        </el-table-column>
        <el-table-column label="原因" min-width="150"><template #default="{ row }">{{ row.reason?.note || row.reason?.code || '-' }}</template></el-table-column>
        <el-table-column label="操作" width="210" align="right">
          <template #default="{ row }">
            <el-button v-if="canExecute && (row.node_type === 'suite' || row.node_type === 'case')" size="small" text type="primary" :disabled="row.effective_status === 'skipped'" @click="runNode(displayNode(row))">运行</el-button>
            <template v-if="canEditProject && !displayNode(row)._isPhaseGroup">
              <el-button v-if="row.status_source === 'direct'" size="small" text @click="restoreRow(displayNode(row))">恢复</el-button>
              <el-button v-else-if="row.status_source !== 'inherited'" size="small" text type="danger" @click="openSkip([displayNode(row)])">跳过</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-if="store.suitePage && store.suitePage.total > store.suitePage.page_size"
        class="suite-pagination"
        background
        layout="prev, pager, next"
        :current-page="store.suitePage.page"
        :page-size="store.suitePage.page_size"
        :total="store.suitePage.total"
        @current-change="onSuitePageChanged"
      />

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
.head-left, .head-right { display: flex; align-items: center; gap: 8px; }
.head-right { flex-wrap: wrap; justify-content: flex-end; }
.selection-summary { color: var(--el-text-color-secondary); font-size: 12px; white-space: nowrap; }
.suite-pagination { justify-content: flex-end; margin-top: 2px; }
.profile-name { font-weight: 600; font-size: 16px; }
.rev { color: var(--el-text-color-secondary); font-size: 12px; }
.node-name { display: inline-flex; align-items: center; }
.node-label { display: inline-flex; flex-direction: column; gap: 2px; }
/* 变量列：竖排展示「变量名：变量值」；编辑图标进入编辑态，编辑态给「保存 / 取消」 */
.case-variable-list { display: flex; flex-direction: column; gap: 4px; padding: 2px 0; }
.case-variable-empty { color: var(--el-text-color-secondary); font-size: 12px; }
.case-variable-item {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 22px;
  padding: 0 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
}
.case-variable-item .variable-name { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.case-variable-item .variable-colon { opacity: 0.7; }
.case-variable-item .variable-value {
  min-width: 40px;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.case-variable-item .variable-input { width: 180px; }
.case-variable-item .variable-icon-button,
.case-variable-item .variable-restore { padding: 0 4px; }
.case-variable-item .variable-icon-button { color: var(--el-text-color-secondary); }
.case-variable-item .variable-icon-button:hover { color: var(--el-color-primary); }
.case-variable-item.tone-overridden { color: #1d4ed8; background: rgba(37, 99, 235, 0.12); border-color: rgba(37, 99, 235, 0.35); }
.case-variable-item.tone-inherited { color: var(--el-text-color-regular); background: rgba(100, 116, 139, 0.08); border-color: rgba(100, 116, 139, 0.24); }
.case-variable-item.tone-undefined { color: #c2410c; background: rgba(249, 115, 22, 0.12); border-color: rgba(249, 115, 22, 0.35); }
.case-variable-item.tone-random { color: #7c3aed; background: rgba(139, 92, 246, 0.14); border-color: rgba(139, 92, 246, 0.38); }
.case-variable-item.tone-mixed { color: #b45309; background: rgba(245, 158, 11, 0.14); border-color: rgba(245, 158, 11, 0.4); }
.element-label, .element-context { color: var(--el-text-color-secondary); font-size: 12px; }
.expand-button { width: 22px; padding: 0; border: 0; background: transparent; cursor: pointer; color: inherit; }
.node-dot { display: inline-block; width: 22px; text-align: center; }
</style>
