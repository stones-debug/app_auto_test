import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getSuiteMock, listVariablesMock } = vi.hoisted(() => ({
  getSuiteMock: vi.fn(),
  listVariablesMock: vi.fn(),
}))

vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))
vi.mock('@/api/suites', () => ({ getSuite: getSuiteMock, listVariables: listVariablesMock }))

import type { Suite } from '@/api/suites'
import { useSuiteDetail } from '@/composables/useSuiteDetail'

function suite(id: number): Suite {
  return {
    id,
    project_id: 1,
    name: `S${id}`,
    status: 'active',
    case_count: 0,
    setup_steps: [],
    teardown_steps: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

beforeEach(() => {
  getSuiteMock.mockReset()
  listVariablesMock.mockReset().mockResolvedValue([])
})

describe('套件详情选择竞态', () => {
  it('旧详情回包不会覆盖最新选择，也不会提前结束 loading', async () => {
    const first = deferred<Suite>()
    const second = deferred<Suite>()
    getSuiteMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const state = useSuiteDetail()

    const oldSelect = state.selectSuite(1)
    const currentSelect = state.selectSuite(2)
    second.resolve(suite(2))
    await currentSelect
    expect(state.suiteDetail.value?.id).toBe(2)
    expect(state.loadingDetail.value).toBe(false)

    first.resolve(suite(1))
    await oldSelect
    expect(state.suiteDetail.value?.id).toBe(2)
    expect(state.loadingDetail.value).toBe(false)
  })

  it('重置详情会让在途回包失效', async () => {
    const pending = deferred<Suite>()
    getSuiteMock.mockReturnValueOnce(pending.promise)
    const state = useSuiteDetail()
    const select = state.selectSuite(1)
    state.reset()
    pending.resolve(suite(1))
    await select
    expect(state.suiteDetail.value).toBeNull()
    expect(state.loadingDetail.value).toBe(false)
  })
})
