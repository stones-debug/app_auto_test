import { computed, ref } from 'vue'

import { listSuites, type Suite } from '@/api/suites'

export type SuiteSortBy = 'updated' | 'name' | 'cases'
export type StatusTag = { label: string; type: 'success' | 'info' | 'warning' }

/** 套件列表的筛选、排序及当前套件选择状态。 */
export function useSuiteList(projectId: number, onSelect?: (id: number) => Promise<void>) {
  const suites = ref<Suite[]>([])
  const loadingSuites = ref(false)
  const activeSuite = ref<number | null>(null)
  const keyword = ref('')
  const sortBy = ref<SuiteSortBy>('updated')

  const filteredSuites = computed(() => {
    const kw = keyword.value.trim().toLowerCase()
    const base = kw
      ? suites.value.filter(
          (suite) =>
            suite.name.toLowerCase().includes(kw) || (suite.description ?? '').toLowerCase().includes(kw),
        )
      : [...suites.value]
    switch (sortBy.value) {
      case 'name':
        return base.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
      case 'cases':
        return base.sort((a, b) => b.case_count - a.case_count)
      default:
        return base.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    }
  })

  function suiteStatusMeta(status: string): StatusTag {
    if (status === 'active') return { label: '启用', type: 'success' }
    if (status === 'disabled') return { label: '停用', type: 'info' }
    return { label: status, type: 'warning' }
  }

  async function selectSuite(id: number) {
    activeSuite.value = id
    await onSelect?.(id)
  }

  async function loadSuites() {
    loadingSuites.value = true
    try {
      suites.value = await listSuites(projectId)
      if (!activeSuite.value && suites.value.length > 0) await selectSuite(suites.value[0].id)
    } finally {
      loadingSuites.value = false
    }
  }

  function clearActiveSuite() {
    activeSuite.value = null
  }

  return {
    suites,
    loadingSuites,
    activeSuite,
    keyword,
    sortBy,
    filteredSuites,
    suiteStatusMeta,
    selectSuite,
    loadSuites,
    clearActiveSuite,
  }
}
