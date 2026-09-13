import { computed, ref, type Ref } from 'vue'

import { addSuiteCases, listSuiteCases, removeSuiteCase, reorderSuiteCases, type SuiteCase } from '@/api/suites'
import { listCases } from '@/api/cases'
import {
  getGroupSelectionState,
  setGroupSelection,
  toggleCollapsedGroup,
} from '@/utils/suiteCaseSelection'
import { moveToPosition } from '@/utils/suiteCaseOrder'

export interface AddCaseCandidate {
  id: number
  name: string
  module_name: string | null
  status: string
}

export interface CaseGroup {
  name: string
  cases: AddCaseCandidate[]
}

export type CaseStatusMeta = { label: string; type: 'success' | 'info' | 'warning' }

export function useSuiteCases(
  projectId: number,
  activeSuite: Ref<number | null>,
  onRefresh?: (preserveSteps?: boolean) => Promise<void>,
) {
  const suiteCases = ref<SuiteCase[]>([])
  const addDialogVisible = ref(false)
  const addKeyword = ref('')
  const allCases = ref<AddCaseCandidate[]>([])
  const selectedIds = ref<Set<number>>(new Set())
  const addingCases = ref(false)
  const loadingAddCases = ref(false)
  const collapsedCaseGroups = ref<Set<string>>(new Set())
  const ordering = ref(false)
  const persistedOrder = ref<number[]>([])
  const casesRefreshVersion = ref(0)
  let reorderRequestId = 0
  let loadRequestId = 0

  const filteredCases = computed(() => {
    const kw = addKeyword.value.trim().toLowerCase()
    if (!kw) return allCases.value
    return allCases.value.filter((item) => item.name.toLowerCase().includes(kw))
  })

  const groupedCases = computed<CaseGroup[]>(() => {
    const map = new Map<string, AddCaseCandidate[]>()
    for (const item of filteredCases.value) {
      const key = item.module_name || '未分组'
      const group = map.get(key)
      if (group) group.push(item)
      else map.set(key, [item])
    }
    return [...map].map(([name, cases]) => ({ name, cases }))
  })

  function caseStatusMeta(status: string): CaseStatusMeta {
    if (status === 'active') return { label: '启用', type: 'success' }
    if (status === 'disabled') return { label: '停用', type: 'info' }
    if (status === 'draft') return { label: '草稿', type: 'warning' }
    return { label: status, type: 'info' }
  }

  async function loadSuiteCases(id: number | null = activeSuite.value) {
    const requestId = ++loadRequestId
    if (!id) {
      suiteCases.value = []
      persistedOrder.value = []
      casesRefreshVersion.value += 1
      return
    }
    const cases = await listSuiteCases(id)
    if (requestId !== loadRequestId || activeSuite.value !== id) return
    suiteCases.value = cases
    persistedOrder.value = suiteCases.value.map((item) => item.id)
    casesRefreshVersion.value += 1
  }

  function resetAddDialog() {
    addKeyword.value = ''
    selectedIds.value = new Set()
    collapsedCaseGroups.value = new Set()
  }

  async function openAddCase() {
    if (!activeSuite.value) return
    const suiteId = activeSuite.value
    resetAddDialog()
    allCases.value = []
    addDialogVisible.value = true
    loadingAddCases.value = true
    try {
      const pageSize = 200
      const firstPage = await listCases(projectId, { page: 1, page_size: pageSize })
      const totalPages = Math.ceil(firstPage.total / pageSize)
      const remainingPages = await Promise.all(
        Array.from({ length: Math.max(0, totalPages - 1) }, (_, index) =>
          listCases(projectId, { page: index + 2, page_size: pageSize }),
        ),
      )
      if (activeSuite.value !== suiteId) return
      allCases.value = [firstPage, ...remainingPages]
        .flatMap((page) => page.items)
        .map((item) => ({ id: item.id, name: item.name, module_name: item.module_name ?? null, status: item.status }))
    } catch {
      ElMessage.error('加载可添加用例失败，请重试')
    } finally {
      loadingAddCases.value = false
    }
  }

  function toggleSelect(id: number) {
    const next = new Set(selectedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selectedIds.value = next
  }

  function groupSelectionState(group: CaseGroup) {
    return getGroupSelectionState(selectedIds.value, group.cases)
  }

  function toggleGroupSelection(group: CaseGroup, selected: boolean) {
    selectedIds.value = setGroupSelection(selectedIds.value, group.cases, selected)
  }

  function isCaseGroupCollapsed(group: CaseGroup) {
    return collapsedCaseGroups.value.has(group.name)
  }

  function toggleCaseGroup(group: CaseGroup) {
    collapsedCaseGroups.value = toggleCollapsedGroup(collapsedCaseGroups.value, group.name)
  }

  async function addSelectedCases() {
    if (!activeSuite.value || selectedIds.value.size === 0) return
    addingCases.value = true
    try {
      await addSuiteCases(activeSuite.value, [...selectedIds.value])
      const count = selectedIds.value.size
      ElMessage.success(`已添加 ${count} 个用例`)
      addDialogVisible.value = false
      resetAddDialog()
      await onRefresh?.(true)
      await loadSuiteCases()
    } finally {
      addingCases.value = false
    }
  }

  async function removeCase(suiteCase: SuiteCase) {
    if (!activeSuite.value) return
    try {
      await ElMessageBox.confirm(
        `确认从套件中移除用例「${suiteCase.case_name}」？`,
        '移除用例',
        { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    await removeSuiteCase(activeSuite.value, suiteCase.id)
    ElMessage.success('已移除用例')
    await onRefresh?.(true)
    await loadSuiteCases()
  }

  async function onReorder() {
    if (!activeSuite.value) return false
    const suiteId = activeSuite.value
    const refreshVersion = casesRefreshVersion.value
    const previousOrder = [...persistedOrder.value]
    const nextOrder = suiteCases.value.map((item) => item.id)
    if (nextOrder.join(',') === persistedOrder.value.join(',')) return false
    ordering.value = true
    const requestId = ++reorderRequestId
    try {
      await reorderSuiteCases(suiteId, nextOrder)
      if (activeSuite.value !== suiteId || casesRefreshVersion.value !== refreshVersion) return false
      persistedOrder.value = nextOrder
      ElMessage.success('用例顺序已保存')
      return true
    } catch {
      if (activeSuite.value !== suiteId || casesRefreshVersion.value !== refreshVersion) return false
      suiteCases.value = [...suiteCases.value].sort(
        (a, b) => previousOrder.indexOf(a.id) - previousOrder.indexOf(b.id),
      )
      ElMessage.error('保存用例顺序失败，已恢复原顺序')
      return false
    } finally {
      if (reorderRequestId === requestId) ordering.value = false
    }
  }

  async function moveCaseToPosition(membershipId: number, position: number) {
    if (!activeSuite.value || ordering.value) return false
    const fromIndex = suiteCases.value.findIndex((item) => item.id === membershipId)
    const next = moveToPosition(suiteCases.value, fromIndex, position)
    if (!next || next.map((item) => item.id).join(',') === suiteCases.value.map((item) => item.id).join(',')) return false
    suiteCases.value = next
    try {
      return await onReorder()
    } catch {
      return false
    }
  }

  return {
    suiteCases,
    addDialogVisible,
    addKeyword,
    allCases,
    selectedIds,
    addingCases,
    loadingAddCases,
    groupedCases,
    toggleSelect,
    groupSelectionState,
    toggleGroupSelection,
    isCaseGroupCollapsed,
    toggleCaseGroup,
    caseStatusMeta,
    openAddCase,
    addSelectedCases,
    removeCase,
    onReorder,
    moveCaseToPosition,
    ordering,
    casesRefreshVersion,
    loadSuiteCases,
  }
}
