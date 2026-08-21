import axios, { type AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'

const TOKEN_KEY = 'access_token'
const REFRESH_KEY = 'refresh_token'

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
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

instance.interceptors.response.use(
  (response) => response.data,
  async (error: AxiosError) => {
    const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined
    const isAuthUrl = config?.url?.includes('/auth/') ?? false
    if (error.response?.status === 401 && config && !isAuthUrl && !config._retried) {
      config._retried = true
      const ok = await refreshToken()
      if (ok) {
        config.headers = config.headers ?? {}
        config.headers.Authorization = `Bearer ${getToken()}`
        return instance.request(config)
      }
    }
    if (error.response?.status === 401 && !isAuthUrl) {
      clearTokens()
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

// 先捕获原始方法引用，避免覆盖实例方法造成递归
const _get = instance.get.bind(instance)
const _post = instance.post.bind(instance)
const _put = instance.put.bind(instance)
const _delete = instance.delete.bind(instance)

export interface RequestInstance {
  get<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
  post<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  put<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  delete<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
}

const request: RequestInstance = {
  get: <T>(url: string, config?: AxiosRequestConfig) =>
    _get(url, config) as unknown as Promise<T>,
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    _post(url, data, config) as unknown as Promise<T>,
  put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    _put(url, data, config) as unknown as Promise<T>,
  delete: <T>(url: string, config?: AxiosRequestConfig) =>
    _delete(url, config) as unknown as Promise<T>,
}

export default request
