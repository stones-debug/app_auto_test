import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import axios from 'axios'

vi.mock('axios', async (importOriginal) => {
  const actual = await importOriginal<typeof import('axios')>()
  const instance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    request: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  }
  return {
    ...actual,
    default: {
      ...actual,
      create: vi.fn(() => instance),
      post: vi.fn(),
    },
  }
})

// request.ts 在模块加载时调用 axios.create —— 先导入被测模块
import { clearTokens, getToken, refreshToken, setTokens } from '@/utils/request'

const mockedAxios = vi.mocked(axios)

describe('CR-14 会话刷新闭环', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  afterEach(() => {
    clearTokens()
  })

  it('刷新成功保存新的 access + refresh（后端轮换）', async () => {
    setTokens('a1', 'r1')
    mockedAxios.post.mockResolvedValueOnce({
      data: { access_token: 'a2', refresh_token: 'r2' },
    })

    const ok = await refreshToken()
    expect(ok).toBe(true)
    expect(localStorage.getItem('access_token')).toBe('a2')
    expect(localStorage.getItem('refresh_token')).toBe('r2')
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: 'r1' })
  })

  it('并发调用只触发一次刷新（单飞）', async () => {
    setTokens('a1', 'r1')
    mockedAxios.post.mockResolvedValue({
      data: { access_token: 'a2', refresh_token: 'r2' },
    })

    const [r1, r2] = await Promise.all([refreshToken(), refreshToken()])
    expect(r1).toBe(true)
    expect(r2).toBe(true)
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('刷新失败清空本地令牌', async () => {
    setTokens('a1', 'r1')
    mockedAxios.post.mockRejectedValueOnce(new Error('401'))

    const ok = await refreshToken()
    expect(ok).toBe(false)
    expect(getToken()).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
  })

  it('无 refresh token 时直接失败且不发请求', async () => {
    const ok = await refreshToken()
    expect(ok).toBe(false)
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })
})
