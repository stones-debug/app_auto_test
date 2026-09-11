import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAppProfileStore } from '@/stores/appProfile'

const mocks = vi.hoisted(() => ({
  listAppProfiles: vi.fn(),
  workspace: vi.fn(),
  workspaceNodes: vi.fn(),
}))

vi.mock('@/api/appProfiles', () => ({
  listAppProfiles: mocks.listAppProfiles,
  workspace: mocks.workspace,
  workspaceNodes: mocks.workspaceNodes,
}))

describe('useAppProfileStore 档案工作台状态', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('载入档案并默认选中第一个', async () => {
    mocks.listAppProfiles.mockResolvedValue([
      { id: 1, name: 'DVR', revision: 3 },
      { id: 2, name: '部标机', revision: 1 },
    ])
    const store = useAppProfileStore()
    store.projectId = 7
    await store.loadProfiles()
    expect(store.profiles).toHaveLength(2)
    expect(store.selectedProfileId).toBe(1)
    expect(store.currentProfile?.name).toBe('DVR')
  })

  it('切换档案清空节点缓存与展开状态', async () => {
    mocks.listAppProfiles.mockResolvedValue([{ id: 1, name: 'A' }, { id: 2, name: 'B' }])
    mocks.workspace.mockResolvedValue({
      profile_revision: 2, test_asset_revision: 10, total: 0, execution_selectable_total: 0, page: 1, page_size: 30, items: [],
    })
    const store = useAppProfileStore()
    store.projectId = 7
    await store.loadProfiles()
    store.childrenByParent = { 'suite:5': [] as never[] }
    store.expandedKeys = new Set(['suite:5'])
    await store.selectProfile(2)
    expect(store.childrenByParent).toEqual({})
    expect(store.expandedKeys.size).toBe(0)
    expect(store.profileRevision).toBe(2)
  })

  it('切换项目时不会保留上一个项目的档案选择', async () => {
    mocks.listAppProfiles
      .mockResolvedValueOnce([{ id: 1, name: '项目 A 档案' }])
      .mockResolvedValueOnce([{ id: 8, name: '项目 B 档案' }])
    const store = useAppProfileStore()
    store.projectId = 7
    await store.loadProfiles()
    expect(store.selectedProfileId).toBe(1)
    store.setSuiteSelected(17, false, 'enabled')

    store.projectId = 9
    await store.loadProfiles()
    expect(store.selectedProfileId).toBe(8)
    expect(store.currentProfile?.name).toBe('项目 B 档案')
    expect(store.excludedSuiteIds.size).toBe(0)
  })

  it('补集取消跨筛选、分页和刷新保留，不按当前页收集套件 ID', async () => {
    mocks.workspace
      .mockResolvedValueOnce({
        profile_revision: 2,
        test_asset_revision: 10,
        total: 2,
        execution_selectable_total: 3,
        page: 1,
        page_size: 2,
        items: [
          { node_type: 'suite', id: 11, name: 'A', effective_status: 'enabled' },
          { node_type: 'suite', id: 12, name: 'B', effective_status: 'enabled' },
        ],
      })
      .mockResolvedValueOnce({
        profile_revision: 2,
        test_asset_revision: 10,
        total: 2,
        execution_selectable_total: 3,
        page: 2,
        page_size: 2,
        items: [{ node_type: 'suite', id: 13, name: 'C', effective_status: 'enabled' }],
      })
      .mockResolvedValueOnce({
        profile_revision: 2,
        test_asset_revision: 10,
        total: 1,
        execution_selectable_total: 3,
        page: 1,
        page_size: 2,
        items: [{ node_type: 'suite', id: 12, name: 'B', effective_status: 'enabled' }],
      })
    const store = useAppProfileStore()
    store.projectId = 7
    store.selectedProfileId = 2

    await store.loadWorkspace(1)
    store.setSuiteSelected(12, false, 'enabled')
    expect([...store.excludedSuiteIds]).toEqual([12])
    expect(store.selectedSuiteCount).toBe(2)

    await store.loadWorkspace(2)
    expect([...store.excludedSuiteIds]).toEqual([12])
    expect(store.isSuiteSelected(13, 'enabled')).toBe(true)

    await store.loadWorkspace(1)
    expect([...store.excludedSuiteIds]).toEqual([12])
    expect(store.selectedSuiteCount).toBe(2)
  })

  it('直接跳过套件从选择补集中移除，恢复和恢复全部回到默认选中', async () => {
    mocks.workspace.mockResolvedValue({
      profile_revision: 2,
      test_asset_revision: 10,
      total: 1,
      execution_selectable_total: 2,
      page: 1,
      page_size: 30,
      items: [{ node_type: 'suite', id: 21, name: '跳过', effective_status: 'skipped' }],
    })
    const store = useAppProfileStore()
    store.projectId = 7
    store.selectedProfileId = 2
    store.setSuiteSelected(21, false, 'enabled')
    expect([...store.excludedSuiteIds]).toEqual([21])

    await store.loadWorkspace()
    expect(store.excludedSuiteIds.size).toBe(0)
    expect(store.isSuiteSelectable(21, 'skipped')).toBe(false)

    store.markSuiteRestored(21)
    expect(store.isSuiteSelected(21, 'enabled')).toBe(true)
    store.setSuiteSelected(22, false, 'enabled')
    store.restoreAllSuiteSelection()
    expect(store.excludedSuiteIds.size).toBe(0)
    expect(store.selectedSuiteCount).toBe(2)
  })

  it('用例子节点缓存包含所属套件并传递祖先套件', async () => {
    mocks.workspaceNodes.mockResolvedValue({ total: 1, page: 1, page_size: 200, items: [{ node_type: 'step', node_key: 'step-1' }] })
    const store = useAppProfileStore()
    store.projectId = 7
    store.selectedProfileId = 2
    await store.loadChildren('case', 15, 3)

    expect(mocks.workspaceNodes).toHaveBeenCalledWith(2, expect.objectContaining({
      parent_type: 'case',
      parent_id: 15,
      ancestor_suite_id: 3,
    }))
    expect(store.childrenByParent['case:3:15']).toHaveLength(1)
  })

  it('用例节点按 suite_case_id 区分重复编排并支持刷新重放', async () => {
    mocks.workspaceNodes.mockResolvedValue({ total: 1, page: 1, page_size: 200, items: [{ node_type: 'step', node_key: 'step-1' }] })
    mocks.workspace.mockResolvedValue({
      profile_revision: 3, test_asset_revision: 11, total: 0, page: 1, page_size: 30, items: [],
    })
    const store = useAppProfileStore()
    store.projectId = 7
    store.selectedProfileId = 2
    store.expandedKeys = new Set(['case:3:99'])
    await store.loadChildren('case', 15, 3, 99)

    // 缓存键与请求都以编排项身份为准
    expect(mocks.workspaceNodes).toHaveBeenCalledWith(2, expect.objectContaining({
      parent_type: 'case',
      parent_id: 15,
      ancestor_suite_id: 3,
      suite_case_id: 99,
    }))
    expect(store.childrenByParent['case:3:99']).toHaveLength(1)

    // 刷新保留展开状态，并按原参数重放子节点加载
    await store.refreshVisibleWorkspace()
    expect(store.expandedKeys.has('case:3:99')).toBe(true)
    expect(store.childrenByParent['case:3:99']).toHaveLength(1)
    expect(mocks.workspaceNodes).toHaveBeenLastCalledWith(2, expect.objectContaining({ suite_case_id: 99 }))
  })

  it('markRevision 更新 revision 且清除 stale', () => {
    const store = useAppProfileStore()
    store.stale = true
    store.markRevision(9, 100)
    expect(store.profileRevision).toBe(9)
    expect(store.testAssetRevision).toBe(100)
    expect(store.stale).toBe(false)
  })

  it('toggleExpand 幂等切换', () => {
    const store = useAppProfileStore()
    store.toggleExpand('suite:1')
    expect(store.expandedKeys.has('suite:1')).toBe(true)
    store.toggleExpand('suite:1')
    expect(store.expandedKeys.has('suite:1')).toBe(false)
  })
})
