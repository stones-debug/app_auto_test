import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  listAppProfiles,
  suiteSteps,
  workspace,
  workspaceNodes,
  type AppProfileSummary,
  type ProfileNode,
  type WorkspacePage,
} from '@/api/appProfiles'

export interface WorkspaceFilters {
  keyword: string
  effective_status: 'all' | 'enabled' | 'skipped' | 'overridden'
  reason_code: string
  sort_by: 'name' | 'updated_at' | 'case_count'
  sort_order: 'asc' | 'desc'
}

// 方案 §5.3：档案工作台状态——切换档案隔离缓存、URL 同步、stale 标记。
export const useAppProfileStore = defineStore('appProfile', () => {
  const projectId = ref<number | null>(null)
  const loadedProjectId = ref<number | null>(null)
  const profiles = ref<AppProfileSummary[]>([])
  const selectedProfileId = ref<number | null>(null)
  const profileRevision = ref<number | null>(null)
  const testAssetRevision = ref<number | null>(null)
  const suitePage = ref<WorkspacePage | null>(null)
  const childrenByParent = ref<Record<string, ProfileNode[]>>({})
  // 记录每个子节点缓存的加载参数，刷新时可原样重放（用例节点身份是 suite_case_id）
  const childArgsByKey = ref<Record<string, { parentType: 'suite' | 'case'; parentId: number; ancestorSuiteId?: number; suiteCaseId?: number }>>({})
  const expandedKeys = ref<Set<string>>(new Set())
  const selectedKeys = ref<Set<string>>(new Set())
  const loading = ref(false)
  const stale = ref(false)
  const filters = ref<WorkspaceFilters>({
    keyword: '',
    effective_status: 'all',
    reason_code: '',
    sort_by: 'name',
    sort_order: 'asc',
  })

  const currentProfile = computed(() =>
    profiles.value.find((p) => p.id === selectedProfileId.value) ?? null,
  )

  async function loadProfiles() {
    if (projectId.value == null) return
    if (loadedProjectId.value !== projectId.value) {
      selectedProfileId.value = null
      suitePage.value = null
      childrenByParent.value = {}
      childArgsByKey.value = {}
      expandedKeys.value = new Set()
      selectedKeys.value = new Set()
      profileRevision.value = null
      testAssetRevision.value = null
      stale.value = false
      loadedProjectId.value = projectId.value
    }
    profiles.value = await listAppProfiles(projectId.value, { include_disabled: false })
    if (!profiles.value.some((profile) => profile.id === selectedProfileId.value)) {
      selectedProfileId.value = profiles.value[0]?.id ?? null
    }
  }

  async function selectProfile(id: number | null) {
    selectedProfileId.value = id
    stale.value = false
    childrenByParent.value = {}
    childArgsByKey.value = {}
    expandedKeys.value = new Set()
    selectedKeys.value = new Set()
    suitePage.value = null
    profileRevision.value = null
    if (id != null) {
      await loadWorkspace()
    }
  }

  async function loadWorkspace() {
    if (projectId.value == null || selectedProfileId.value == null) return
    loading.value = true
    try {
      const page = await workspace(selectedProfileId.value, {
        page: 1,
        page_size: 30,
        keyword: filters.value.keyword || undefined,
        effective_status: filters.value.effective_status,
        reason_code: filters.value.reason_code || undefined,
        sort_by: filters.value.sort_by,
        sort_order: filters.value.sort_order,
      })
      suitePage.value = page
      profileRevision.value = page.profile_revision
      testAssetRevision.value = page.test_asset_revision
    } finally {
      loading.value = false
    }
  }

  async function loadChildren(
    parentType: 'suite' | 'case',
    parentId: number,
    ancestorSuiteId?: number,
    suiteCaseId?: number,
    force = false,
  ): Promise<ProfileNode[]> {
    if (projectId.value == null || selectedProfileId.value == null) return []
    // 用例节点的缓存键用编排项身份（suite_case_id），同一用例重复编排互不覆盖
    const identity = parentType === 'case' ? (suiteCaseId ?? parentId) : parentId
    const key = parentType === 'case' ? `case:${ancestorSuiteId ?? 0}:${identity}` : `suite:${parentId}`
    if (!force && childrenByParent.value[key]) return childrenByParent.value[key]
    const page = await workspaceNodes(selectedProfileId.value, {
      parent_type: parentType,
      parent_id: parentId,
      ancestor_suite_id: parentType === 'case' ? ancestorSuiteId : undefined,
      suite_case_id: parentType === 'case' ? suiteCaseId : undefined,
      page_size: 200,
    })
    childrenByParent.value = { ...childrenByParent.value, [key]: page.items }
    childArgsByKey.value = {
      ...childArgsByKey.value,
      [key]: { parentType, parentId, ancestorSuiteId, suiteCaseId },
    }
    return page.items
  }

  // 套件前后置步骤懒加载：写入 `suite:<sid>:setup|teardown` 伪节点缓存。
  async function loadSuiteSteps(
    suiteId: number,
    phase: 'suite_setup' | 'suite_teardown',
    force = false,
  ): Promise<ProfileNode[]> {
    if (projectId.value == null || selectedProfileId.value == null) return []
    const key = `suite:${suiteId}:${phase === 'suite_setup' ? 'setup' : 'teardown'}`
    if (!force && childrenByParent.value[key]) return childrenByParent.value[key]
    const page = await suiteSteps(selectedProfileId.value, suiteId, { phase })
    childrenByParent.value = { ...childrenByParent.value, [key]: page.items }
    return page.items
  }

  async function refreshVisibleWorkspace() {
    const expanded = [...expandedKeys.value]
    const previousArgs = { ...childArgsByKey.value }
    childrenByParent.value = {}
    childArgsByKey.value = {}
    await loadWorkspace()
    for (const key of expanded) {
      const args = previousArgs[key]
      if (args) {
        await loadChildren(args.parentType, args.parentId, args.ancestorSuiteId, args.suiteCaseId)
        continue
      }
      const parts = key.split(':')
      if (parts[0] === 'suite') {
        if (parts.length === 3 && (parts[2] === 'setup' || parts[2] === 'teardown')) {
          await loadSuiteSteps(Number(parts[1]), parts[2] === 'setup' ? 'suite_setup' : 'suite_teardown')
        } else {
          await loadChildren('suite', Number(parts[1]))
        }
      }
    }
  }

  function toggleExpand(key: string) {
    const next = new Set(expandedKeys.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    expandedKeys.value = next
  }

  function setStale() {
    stale.value = true
  }

  function markRevision(rev: number, assetRev: number) {
    profileRevision.value = rev
    testAssetRevision.value = assetRev
    stale.value = false
  }

  function reset() {
    projectId.value = null
    loadedProjectId.value = null
    profiles.value = []
    selectedProfileId.value = null
    suitePage.value = null
    childrenByParent.value = {}
    childArgsByKey.value = {}
    expandedKeys.value = new Set()
    selectedKeys.value = new Set()
    stale.value = false
  }

  return {
    projectId,
    profiles,
    selectedProfileId,
    profileRevision,
    testAssetRevision,
    suitePage,
    childrenByParent,
    expandedKeys,
    selectedKeys,
    loading,
    stale,
    filters,
    currentProfile,
    loadProfiles,
    selectProfile,
    loadWorkspace,
    loadChildren,
    loadSuiteSteps,
    refreshVisibleWorkspace,
    toggleExpand,
    setStale,
    markRevision,
    reset,
  }
})
