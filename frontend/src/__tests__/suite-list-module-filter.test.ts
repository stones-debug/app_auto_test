import { beforeEach, describe, expect, it, vi } from 'vitest'

const { listSuitesMock } = vi.hoisted(() => ({ listSuitesMock: vi.fn() }))

vi.mock('@/api/suites', () => ({
  listSuites: listSuitesMock,
}))

import type { Suite } from '@/api/suites'
import { SUITE_LIST_PAGE_SIZE, useSuiteList } from '@/composables/useSuiteList'

function suite(id: number, name = `S${id}`): Suite {
  return {
    id,
    project_id: 1,
    name,
    status: 'active',
    case_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function pageOf(items: Suite[], total = items.length) {
  return { total, page: 1, page_size: SUITE_LIST_PAGE_SIZE, items }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  listSuitesMock.mockReset()
  listSuitesMock.mockResolvedValue(pageOf([suite(1)]))
})

describe('套件列表的模块筛选与分页', () => {
  it('默认「全部」：只带分页参数，不下发 module_id/ungrouped', async () => {
    const list = useSuiteList(1)
    await list.loadSuites()

    expect(listSuitesMock).toHaveBeenCalledTimes(1)
    const [projectId, params] = listSuitesMock.mock.calls[0]
    expect(projectId).toBe(1)
    expect(params).toMatchObject({ page: 1, page_size: SUITE_LIST_PAGE_SIZE })
    expect(params).not.toHaveProperty('module_id')
    expect(params).not.toHaveProperty('ungrouped')
  })

  it('选中「未分组」时下发 ungrouped=true', async () => {
    const list = useSuiteList(1)
    await list.setModuleKey('none')

    expect(listSuitesMock.mock.calls[0][1]).toMatchObject({ ungrouped: true })
  })

  it('选中具体模块时下发 module_id', async () => {
    const list = useSuiteList(1)
    await list.setModuleKey('12')

    const params = listSuitesMock.mock.calls[0][1]
    expect(params).toMatchObject({ module_id: 12 })
    expect(params).not.toHaveProperty('ungrouped')
  })

  it('关键字在服务端过滤，空关键字不下发', async () => {
    const list = useSuiteList(1)
    list.keyword.value = '  冒烟  '
    await list.loadSuites()
    expect(listSuitesMock.mock.calls[0][1]).toMatchObject({ keyword: '冒烟' })

    list.keyword.value = '   '
    await list.loadSuites()
    expect(listSuitesMock.mock.calls[1][1].keyword).toBeUndefined()
  })

  it('被 200 上限截断时给出标记', async () => {
    listSuitesMock.mockResolvedValue(pageOf([suite(1), suite(2)], 5))
    const list = useSuiteList(1)
    await list.loadSuites()
    expect(list.truncated.value).toBe(true)

    listSuitesMock.mockResolvedValue(pageOf([suite(1)]))
    await list.loadSuites()
    expect(list.truncated.value).toBe(false)
  })
})

describe('筛选变化后的选中套件', () => {
  it('当前套件被筛选条件排除时自动切到第一条', async () => {
    const onSelect = vi.fn(async () => undefined)
    listSuitesMock.mockResolvedValue(pageOf([suite(1), suite(2)]))
    const list = useSuiteList(1, onSelect)
    await list.loadSuites()
    expect(list.activeSuite.value).toBe(1)

    listSuitesMock.mockResolvedValue(pageOf([suite(7)]))
    await list.setModuleKey('7')
    expect(list.activeSuite.value).toBe(7)
    expect(onSelect).toHaveBeenLastCalledWith(7)
  })

  it('筛选结果为空时清空选中，不残留上一个套件的详情', async () => {
    listSuitesMock.mockResolvedValue(pageOf([suite(1)]))
    const list = useSuiteList(1)
    await list.loadSuites()

    listSuitesMock.mockResolvedValue(pageOf([], 0))
    await list.setModuleKey('none')
    expect(list.activeSuite.value).toBeNull()
    expect(list.suites.value).toEqual([])
  })

  it('已加载的当前套件仍在结果里时不重复切换', async () => {
    const onSelect = vi.fn(async () => undefined)
    listSuitesMock.mockResolvedValue(pageOf([suite(1), suite(2)]))
    const list = useSuiteList(1, onSelect)
    await list.loadSuites()
    onSelect.mockClear()

    await list.loadSuites()
    expect(onSelect).not.toHaveBeenCalled()
    expect(list.activeSuite.value).toBe(1)
  })

  it('模块切换请求期间清空上一分组，完成后再显示目标分组首个套件', async () => {
    listSuitesMock.mockResolvedValueOnce(pageOf([suite(1)]))
    const list = useSuiteList(1)
    await list.loadSuites()

    const next = deferred<ReturnType<typeof pageOf>>()
    listSuitesMock.mockReturnValueOnce(next.promise)
    const change = list.setModuleKey('7')

    expect(list.suites.value).toEqual([])
    expect(list.activeSuite.value).toBeNull()
    expect(list.loadingSuites.value).toBe(true)

    next.resolve(pageOf([suite(7)]))
    await change
    expect(list.suites.value.map((item) => item.id)).toEqual([7])
    expect(list.activeSuite.value).toBe(7)
  })

  it('快速切换模块时旧回包不覆盖最新列表，也不提前结束 loading', async () => {
    const first = deferred<ReturnType<typeof pageOf>>()
    const second = deferred<ReturnType<typeof pageOf>>()
    listSuitesMock.mockReset()
    listSuitesMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const list = useSuiteList(1)

    const oldChange = list.setModuleKey('1')
    const newestChange = list.setModuleKey('2')
    second.resolve(pageOf([suite(2)]))
    await newestChange
    expect(list.suites.value.map((item) => item.id)).toEqual([2])
    expect(list.activeSuite.value).toBe(2)
    expect(list.loadingSuites.value).toBe(false)

    first.resolve(pageOf([suite(1)]))
    await oldChange
    expect(list.suites.value.map((item) => item.id)).toEqual([2])
    expect(list.activeSuite.value).toBe(2)
  })
})
