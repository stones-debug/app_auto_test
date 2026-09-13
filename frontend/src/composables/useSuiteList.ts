import { computed, ref } from 'vue'

import { listSuites, type Suite } from '@/api/suites'
import { moduleFilterParams, type ModuleKey } from '@/utils/moduleFilter'

export type SuiteSortBy = 'updated' | 'name' | 'cases'
export type StatusTag = { label: string; type: 'success' | 'info' | 'warning' }

/** 侧栏一次拉取的套件上限（后端 `max_page_size = 200`）。 */
export const SUITE_LIST_PAGE_SIZE = 200

/**
 * 套件列表的筛选、排序及当前套件选择状态。
 *
 * 模块筛选（`moduleKey`）与关键字都下发给服务端：侧栏不是分页列表，只在本地
 * 过滤已加载的一页会让"某个模块明明有套件却显示空"。
 */
export function useSuiteList(projectId: number, onSelect?: (id: number) => Promise<void>) {
  const suites = ref<Suite[]>([])
  const loadingSuites = ref(false)
  const activeSuite = ref<number | null>(null)
  const keyword = ref('')
  const sortBy = ref<SuiteSortBy>('updated')
  const moduleKey = ref<ModuleKey>('all')
  /** 结果被 200 上限截断时为 true：此后的排序/计数只对该子集成立 */
  const truncated = ref(false)

  const filteredSuites = computed(() => {
    const base = [...suites.value]
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
      const data = await listSuites(projectId, {
        page: 1,
        page_size: SUITE_LIST_PAGE_SIZE,
        keyword: keyword.value.trim() || undefined,
        ...moduleFilterParams(moduleKey.value),
      })
      suites.value = data.items
      truncated.value = data.total > data.items.length
      const stillVisible = suites.value.some((suite) => suite.id === activeSuite.value)
      if (!stillVisible) {
        // 当前套件被筛选条件排除或已被删除：切到第一条，没有则清空
        if (suites.value.length > 0) await selectSuite(suites.value[0].id)
        else activeSuite.value = null
      }
    } finally {
      loadingSuites.value = false
    }
  }

  async function setModuleKey(key: ModuleKey) {
    moduleKey.value = key
    await loadSuites()
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
    moduleKey,
    truncated,
    filteredSuites,
    suiteStatusMeta,
    selectSuite,
    setModuleKey,
    loadSuites,
    clearActiveSuite,
  }
}
