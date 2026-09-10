import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

import { useListQuery } from '@/composables/useListQuery'

type Page = { total: number; items: Array<{ id: number }> }

/** 手动控制每个请求的返回时机，用于复现响应乱序。 */
function createDeferredFetch() {
  const deferred: Array<{ resolve: (value: Page) => void; reject: (error: unknown) => void }> = []
  const fetch = vi.fn(
    // 显式接收 params：既与 useListQuery 调用签名一致，也便于断言请求参数
    (_params: Record<string, unknown>) =>
      new Promise<Page>((resolve, reject) => {
        deferred.push({ resolve, reject })
      }),
  )
  return { fetch, deferred }
}

/** 让挂起的微任务全部结算。 */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  vi.clearAllMocks()
  // 仓库未安装 @vue/test-utils，composable 只能在 setup 之外直接调用，
  // onBeforeUnmount 会打一条「no active component instance」提示 —— 属已知噪音，定向过滤；
  // 其它警告照常输出，避免把真实问题一起吞掉。
  vi.spyOn(console, 'warn').mockImplementation((message?: unknown, ...rest: unknown[]) => {
    if (String(message).includes('onBeforeUnmount is called when there is no active component instance')) {
      return
    }
    console.info(message, ...rest)
  })
})

describe('useListQuery 请求竞态', () => {
  it('旧请求后到时被丢弃，不覆盖新请求的数据', async () => {
    const { fetch, deferred } = createDeferredFetch()
    const state = useListQuery<{ id: number }>('/cases', { fetch })

    const first = state.load() // 请求 A：page 1
    state.onPageChange(2) // 请求 B：page 2
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[0][0]).toMatchObject({ page: 1 })
    expect(fetch.mock.calls[1][0]).toMatchObject({ page: 2 })

    // 新请求先返回
    deferred[1].resolve({ total: 2, items: [{ id: 20 }, { id: 21 }] })
    await flush()
    expect(state.items.value).toEqual([{ id: 20 }, { id: 21 }])

    // 旧请求后到：必须被丢弃，否则页面会显示 page 1 的数据却停在 page 2
    deferred[0].resolve({ total: 2, items: [{ id: 10 }, { id: 11 }] })
    await first
    await flush()
    expect(state.items.value).toEqual([{ id: 20 }, { id: 21 }])
    expect(state.page.value).toBe(2)
  })

  it('旧请求完成不会提前关闭新请求的 loading', async () => {
    const { fetch, deferred } = createDeferredFetch()
    const state = useListQuery<{ id: number }>('/cases', { fetch })

    const first = state.load()
    state.onPageChange(2)

    deferred[0].resolve({ total: 0, items: [] })
    await first
    expect(state.loading.value).toBe(true) // 新请求仍在途

    deferred[1].resolve({ total: 0, items: [] })
    await flush()
    expect(state.loading.value).toBe(false)
  })

  it('旧请求失败不会覆盖新请求的成功结果', async () => {
    const { fetch, deferred } = createDeferredFetch()
    const state = useListQuery<{ id: number }>('/cases', { fetch })

    const first = state.load()
    state.onPageChange(2)

    deferred[1].resolve({ total: 1, items: [{ id: 20 }] })
    await flush()

    deferred[0].reject(new Error('旧请求超时'))
    await first
    await flush()
    expect(state.error.value).toBeNull()
    expect(state.items.value).toEqual([{ id: 20 }])
  })

  it('顺序返回时后发请求的结果正常生效', async () => {
    const { fetch, deferred } = createDeferredFetch()
    const state = useListQuery<{ id: number }>('/cases', { fetch })

    const first = state.load()
    deferred[0].resolve({ total: 1, items: [{ id: 10 }] })
    await first
    expect(state.items.value).toEqual([{ id: 10 }])

    state.onPageChange(2)
    deferred[1].resolve({ total: 1, items: [{ id: 20 }] })
    await flush()
    expect(state.items.value).toEqual([{ id: 20 }])
  })
})
