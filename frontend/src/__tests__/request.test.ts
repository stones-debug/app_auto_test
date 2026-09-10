import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import axios from 'axios'

const errorMessage = vi.hoisted(() => vi.fn())

vi.mock('element-plus', () => ({
  ElMessage: { error: errorMessage },
}))

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
import { apiErrorMessage, clearTokens, getToken, refreshToken, setTokens } from '@/utils/request'

const mockedAxios = vi.mocked(axios, true)

// 模块加载时 axios.create 已调用一次；此处捕获实例与 401 处理器（beforeEach 的 clearAllMocks 会清空 mock.results）
const axInstance = mockedAxios.create.mock.results[0].value
const responseErrorHandler = axInstance.interceptors.response.use.mock.calls.at(-1)[1]

function authError(url: string) {
  return { config: { url, headers: {} }, response: { status: 401 } }
}

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

  it('/auth/me 401 → 尝试刷新；刷新成功则重放原请求（会话恢复）', async () => {
    setTokens('a1', 'r1')
    const handler = responseErrorHandler
    mockedAxios.post.mockResolvedValueOnce({ data: { access_token: 'a2', refresh_token: 'r2' } })
    axInstance.request.mockResolvedValueOnce({ data: { id: 1, username: 'u' } })

    const result = await handler(authError('/auth/me'))
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: 'r1' })
    expect(axInstance.request).toHaveBeenCalled()
    expect(result).toEqual({ data: { id: 1, username: 'u' } })
  })

  it('/auth/me 401 → 刷新失败 → 清空令牌并跳转登录页', async () => {
    setTokens('a1', 'r1')
    const handler = responseErrorHandler
    mockedAxios.post.mockRejectedValueOnce(new Error('401'))

    const fakeWindow = {
      location: { pathname: '/projects/7', search: '', href: '' },
    }
    const originalWindow = globalThis.window
    Object.defineProperty(globalThis, 'window', { value: fakeWindow, configurable: true })

    try {
      await expect(handler(authError('/auth/me'))).rejects.toBeTruthy()
    } finally {
      Object.defineProperty(globalThis, 'window', { value: originalWindow, configurable: true })
    }
    expect(getToken()).toBeNull()
    expect(fakeWindow.location.href).toBe('/login?redirect=%2Fprojects%2F7')
  })

  it('/auth/login 401 → 不触发刷新也不跳转（登录失败由页面处理）', async () => {
    setTokens('a1', 'r1')
    const handler = responseErrorHandler

    await expect(handler(authError('/auth/login'))).rejects.toBeTruthy()
    expect(mockedAxios.post).not.toHaveBeenCalled()
    expect(axInstance.request).not.toHaveBeenCalled()
  })

  it('普通接口失败时显示后端返回的校验提示', async () => {
    const handler = responseErrorHandler
    const error = {
      config: { url: '/projects', headers: {} },
      response: { status: 422, data: { detail: [{ msg: '项目名称不能为空' }] } },
    }

    await expect(handler(error)).rejects.toBe(error)
    expect(errorMessage).toHaveBeenCalledWith('项目名称不能为空')
  })

  it('无法连接服务时显示网络提示', async () => {
    const handler = responseErrorHandler
    const error = { config: { url: '/projects', headers: {} }, code: 'ERR_NETWORK' }

    await expect(handler(error)).rejects.toBe(error)
    expect(errorMessage).toHaveBeenCalledWith('无法连接后端服务，请检查网络连接或服务是否已启动')
  })

  it('标记 suppressGlobalError 时不重复弹出网络提示', async () => {
    const handler = responseErrorHandler
    const error = {
      config: { url: '/reports/673/download?download_ts=1', headers: {}, suppressGlobalError: true },
      code: 'ERR_NETWORK',
    }

    await expect(handler(error)).rejects.toBe(error)
    expect(errorMessage).not.toHaveBeenCalled()
  })

  it('apiErrorMessage 支持 FastAPI 字符串错误详情', () => {
    expect(apiErrorMessage({ response: { data: { detail: '项目不存在' } } })).toBe('项目不存在')
  })
})
