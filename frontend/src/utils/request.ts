import axios, { type AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

export type ApiRequestConfig = AxiosRequestConfig & {
  /** 由页面自行展示错误时，禁止请求层重复弹出网络错误。 */
  suppressGlobalError?: boolean
}

const TOKEN_KEY = 'access_token'
const REFRESH_KEY = 'refresh_token'

export interface ApiErrorDetail {
  code: string
  message: string
  context: Record<string, unknown>
}

export function apiErrorDetail(error: unknown): ApiErrorDetail | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null
  const value = detail as { code?: unknown; message?: unknown; context?: unknown }
  if (typeof value.code !== 'string') return null
  return {
    code: value.code,
    message: typeof value.message === 'string' ? value.message : '',
    context: value.context && typeof value.context === 'object' && !Array.isArray(value.context)
      ? value.context as Record<string, unknown>
      : {},
  }
}

export function apiErrorMessage(error: unknown, fallback = '请求失败'): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return ''
        const message = (item as { msg?: unknown }).msg
        return typeof message === 'string' ? message : ''
      })
      .filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  const structured = apiErrorDetail(error)
  if (structured?.message) return structured.message
  const message = (error as { message?: unknown })?.message
  return typeof message === 'string' && message.trim() ? message : fallback
}

function notifyRequestError(error: AxiosError): void {
  const code = error.code
  if (code === 'ERR_CANCELED') return
  if (code === 'ECONNABORTED' || code === 'ETIMEDOUT') {
    ElMessage.error('请求超时，请检查网络或后端服务是否正常')
    return
  }
  if (!error.response) {
    ElMessage.error('无法连接后端服务，请检查网络连接或服务是否已启动')
    return
  }
  const status = error.response.status
  const fallback = status >= 500 ? `服务异常（${status}），请稍后重试` : `请求失败（${status}）`
  ElMessage.error(apiErrorMessage(error, fallback))
}

// V2 §5.1：记住我——勾选用 localStorage（持久），否则 refresh token 放 sessionStorage。
let persistRefresh = localStorage.getItem('remember_me') !== '0'

export function setRememberMe(remember: boolean): void {
  persistRefresh = remember
  localStorage.setItem('remember_me', remember ? '1' : '0')
}

function refreshStorage() {
  return persistRefresh ? localStorage : sessionStorage
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getRefreshToken(): string | null {
  return refreshStorage().getItem(REFRESH_KEY)
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(TOKEN_KEY, access)
  refreshStorage().setItem(REFRESH_KEY, refresh)
}

export function clearTokens(): void {
  localStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

const instance = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

instance.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// CR-14：单飞 refresh 队列——并发 401 只触发一次刷新，成功后重放原请求并保存新 refresh
let refreshPromise: Promise<boolean> | null = null

export async function refreshToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async () => {
    const refresh = getRefreshToken()
    if (!refresh) return false
    try {
      const res = await axios.post<{ access_token: string; refresh_token: string }>('/api/auth/refresh', {
        refresh_token: refresh,
      })
      setTokens(res.data.access_token, res.data.refresh_token)
      return true
    } catch {
      clearTokens()
      return false
    }
  })()
  // 仅在仍指向同一实例时清空，避免异步 IIFE 在赋值前同步完成后被覆盖残留
  const p = refreshPromise
  p.finally(() => {
    if (refreshPromise === p) refreshPromise = null
  })
  return refreshPromise
}

// 登录/注册/刷新/登出端点不应触发会话恢复或跳转登录（避免循环和登录失败误跳转）。
// /auth/me 属于受保护端点：过期时尝试刷新，刷新失败则清空令牌并回到登录页。
const AUTH_NO_SESSION_URLS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout']

function isAuthNoSession(url: string): boolean {
  return AUTH_NO_SESSION_URLS.some((p) => url.includes(p))
}

instance.interceptors.response.use(
  (response) => response.data,
  async (error: AxiosError) => {
    const config = error.config as (InternalAxiosRequestConfig & {
      _retried?: boolean
      suppressGlobalError?: boolean
    }) | undefined
    const url = config?.url ?? ''
    const noSession = isAuthNoSession(url)
    if (error.response?.status === 401 && config && !noSession && !config._retried) {
      config._retried = true
      const ok = await refreshToken()
      if (ok) {
        config.headers = config.headers ?? {}
        config.headers.Authorization = `Bearer ${getToken()}`
        return instance.request(config)
      }
    }
    if (error.response?.status === 401 && !noSession) {
      clearTokens()
      if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
        const path = window.location.pathname + window.location.search
        window.location.href =
          path && path !== '/' ? `/login?redirect=${encodeURIComponent(path)}` : '/login'
      }
    }
    // 页面加载请求通常没有单独的 catch，统一在请求层给用户可见反馈。
    // 登录、注册等认证入口由页面展示业务提示，避免出现重复消息。
    if (!config?.suppressGlobalError && !noSession && error.response?.status !== 401) {
      notifyRequestError(error)
    }
    return Promise.reject(error)
  },
)

// 先捕获原始方法引用，避免覆盖实例方法造成递归
const _get = instance.get.bind(instance)
const _post = instance.post.bind(instance)
const _put = instance.put.bind(instance)
const _patch = instance.patch.bind(instance)
const _delete = instance.delete.bind(instance)

export interface RequestInstance {
  get<T = unknown>(url: string, config?: ApiRequestConfig): Promise<T>
  post<T = unknown>(url: string, data?: unknown, config?: ApiRequestConfig): Promise<T>
  put<T = unknown>(url: string, data?: unknown, config?: ApiRequestConfig): Promise<T>
  patch<T = unknown>(url: string, data?: unknown, config?: ApiRequestConfig): Promise<T>
  delete<T = unknown>(url: string, config?: ApiRequestConfig): Promise<T>
}

const request: RequestInstance = {
  get: <T>(url: string, config?: ApiRequestConfig) =>
    _get(url, config) as unknown as Promise<T>,
  post: <T>(url: string, data?: unknown, config?: ApiRequestConfig) =>
    _post(url, data, config) as unknown as Promise<T>,
  put: <T>(url: string, data?: unknown, config?: ApiRequestConfig) =>
    _put(url, data, config) as unknown as Promise<T>,
  patch: <T>(url: string, data?: unknown, config?: ApiRequestConfig) =>
    _patch(url, data, config) as unknown as Promise<T>,
  delete: <T>(url: string, config?: ApiRequestConfig) =>
    _delete(url, config) as unknown as Promise<T>,
}

export default request
