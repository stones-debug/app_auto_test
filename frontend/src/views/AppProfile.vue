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
  restoreNodeOverride,
  skipRulesBatch,
  upsertNodeOverride,
  type ProfileNode,
  type SkipTarget,
  workspace,
} from '@/api/appProfiles'
import { usePermission } from '@/composables/usePermission'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'
import { useAppProfileStore } from '@/stores/appProfile'
import { buildProfileSkipTarget } from '@/utils/appProfileSkip'

interface DisplayNode extends ProfileNode {
  _key: string
  _depth: number
  _suiteId?: number
  _caseId?: number
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

const nodeOverrideDialog = reactive({
  visible: false,
  row: null as DisplayNode | null,
  patchText: '{}',
  overridden: false,
})

const displayRows = computed<DisplayNode[]>(() => {
  const rows: DisplayNode[] = []
  for (const suite of store.suitePage?.items ?? []) {
    if (suite.id == null) continue
    const suiteId = suite.id
    rows.push({ ...suite, _key: `suite:${suiteId}`, _depth: 0, _suiteId: suiteId })
    if (!store.expandedKeys.has(`suite:${suiteId}`)) continue
    for (const testCase of store.childrenByParent[`suite:${suiteId}`] ?? []) {
      if (testCase.id == null) continue
      const caseId = testCase.id
      const caseKey = `case:${suiteId}:${caseId}`
      rows.push({ ...testCase, _key: caseKey, _depth: 1, _suiteId: suiteId, _caseId: caseId })
      if (!store.expandedKeys.has(caseKey)) continue
      for (const node of store.childrenByParent[caseKey] ?? []) {
        const nodeKey = node.node_key ?? String(node.id)
        rows.push({
          ...node,
          _key: `${node.node_type}:${suiteId}:${caseId}:${nodeKey}`,
          _depth: 2,
          _suiteId: suiteId,
          _caseId: caseId,
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
  if (!row.has_children || row.id == null) return
  if (row.node_type === 'suite') {
    if (!store.expandedKeys.has(row._key)) await store.loadChildren('suite', row.id)
  } else if (row.node_type === 'case' && row._suiteId != null) {
    if (!store.expandedKeys.has(row._key)) await store.loadChildren('case', row.id, row._suiteId)
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

async function openNodeOverride(row: DisplayNode) {
  if (!store.selectedProfileId || !row._caseId || !row.node_key) return
  const data = await listProfileOverrides(store.selectedProfileId)
  const existing = data.nodes.find((item) => (
    item.case_id === row._caseId && item.node_type === row.node_type && item.node_key === row.node_key
  ))
  nodeOverrideDialog.row = row
  nodeOverrideDialog.patchText = JSON.stringify(existing?.patch ?? {}, null, 2)
  nodeOverrideDialog.overridden = Boolean(existing)
  nodeOverrideDialog.visible = true
}

async function saveNodeOverride() {
  const row = nodeOverrideDialog.row
  if (!store.selectedProfileId || store.profileRevision == null || !row?._caseId || !row.node_key) return
  let patch: Record<string, unknown>
  try {
    patch = JSON.parse(nodeOverrideDialog.patchText) as Record<string, unknown>
    if (!patch || Array.isArray(patch) || typeof patch !== 'object') throw new Error()
  } catch {
    ElMessage.warning('覆盖内容必须是 JSON 对象')
    return
  }
  const result = await upsertNodeOverride(
    store.selectedProfileId,
    row._caseId,
    row.node_type as 'step' | 'assertion',
    row.node_key,
    { expected_revision: store.profileRevision, patch },
  )
  store.markRevision(result.revision, store.testAssetRevision ?? 1)
  nodeOverrideDialog.visible = false
  await store.refreshVisibleWorkspace()
  ElMessage.success('节点覆盖已保存')
}

async function restoreNode() {
  const row = nodeOverrideDialog.row
  if (!store.selectedProfileId || store.profileRevision == null || !row?._caseId || !row.node_key) return
  await restoreNodeOverride(
    store.selectedProfileId,
    row._caseId,
    row.node_type as 'step' | 'assertion',
    row.node_key,
    { expected_revision: store.profileRevision },
  )
  const profile = await getAppProfile(store.selectedProfileId)
  store.markRevision(profile.revision, store.testAssetRevision ?? 1)
  nodeOverrideDialog.visible = false
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
        expected_profile_revision: store.profileRevision,
        expected_test_asset_revision: store.testAssetRevision,
      },
    },
  )
  if (execution) await router.push(navigation.executionDetail(execution.id))
}

async function runAllSuites() {
  if (!store.selectedProfileId) return
  runAllLoading.value = true
  try {
    const suiteIds: number[] = []
    let pageNumber = 1
    let total = 0
    do {
      const page = await workspace(store.selectedProfileId, {
        page: pageNumber,
        page_size: 200,
        effective_status: 'all',
        sort_by: 'name',
        sort_order: 'asc',
      })
      suiteIds.push(...page.items.flatMap((item) => item.id == null ? [] : [item.id]))
      total = page.total
      store.markRevision(page.profile_revision, page.test_asset_revision)
      pageNumber += 1
    } while (suiteIds.length < total)

    if (suiteIds.length === 0) {
      ElMessage.warning('当前项目没有可执行的测试套件')
      return
    }
    const page = await listReleases(store.selectedProfileId, { status: 'active', page_size: 100 })
    const release = page.items[0]
    if (!release) {
      ElMessage.warning('当前档案没有可用的发布版本，请先创建发布版本')
      return
    }
    const execution = await devicePicker.value?.open(
      { kind: 'batch', suiteIds, name: `${store.currentProfile?.name ?? '当前 APP'}全部套件` },
      {
        profile: {
          app_profile_id: store.selectedProfileId,
          app_release_id: release.id,
          expected_profile_revision: store.profileRevision ?? 1,
          expected_test_asset_revision: store.testAssetRevision ?? 1,
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
        <el-table-column v-if="canEditProject" type="selection" width="42" />
        <el-table-column label="名称" min-width="260">
          <template #default="{ row }">
            <span class="node-name" :style="{ paddingLeft: `${row._depth * 22}px` }">
              <button v-if="row.has_children" type="button" class="expand-button" @click="toggleNode(displayNode(row))">{{ store.expandedKeys.has(row._key) ? '▾' : '▸' }}</button>
              <span v-else class="node-dot">·</span>{{ row.name }}
            </span>
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
            <template v-if="canEditProject">
              <el-button v-if="row.node_type === 'step' || row.node_type === 'assertion'" size="small" text @click="openNodeOverride(displayNode(row))">覆盖</el-button>
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

      <el-dialog v-model="nodeOverrideDialog.visible" title="步骤/断言参数覆盖" width="560px">
        <p class="override-help">填写与原节点合并的 JSON 对象。字段名及类型会由服务端 Registry 校验。</p>
        <el-input v-model="nodeOverrideDialog.patchText" type="textarea" :rows="12" />
        <template #footer>
          <el-button v-if="nodeOverrideDialog.overridden" type="danger" plain @click="restoreNode">恢复公共配置</el-button>
          <el-button @click="nodeOverrideDialog.visible = false">取消</el-button><el-button type="primary" @click="saveNodeOverride">保存覆盖</el-button>
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
.rev, .override-help { color: var(--el-text-color-secondary); font-size: 12px; }
.batch-bar { padding: 8px 12px; border-radius: 6px; background: var(--el-color-primary-light-9); }
.node-name { display: inline-flex; align-items: center; }
.expand-button { width: 22px; padding: 0; border: 0; background: transparent; cursor: pointer; color: inherit; }
.node-dot { display: inline-block; width: 22px; text-align: center; }
.override-help { margin: 0 0 10px; }
</style>
