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
      profile_revision: 2, test_asset_revision: 10, total: 0, page: 1, page_size: 30, items: [],
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
