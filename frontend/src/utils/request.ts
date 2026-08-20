import axios, { type AxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'

const TOKEN_KEY = 'access_token'
const REFRESH_KEY = 'refresh_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(TOKEN_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens(): void {
  localStorage.removeItem(TOKEN_KEY)
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

instance.interceptors.response.use(
  (response) => response.data,
  async (error: AxiosError) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/auth/')) {
      clearTokens()
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export interface RequestInstance {
  <T = unknown>(config: AxiosRequestConfig): Promise<T>
  get<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
  post<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  put<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  delete<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T>
}

const request = instance as unknown as RequestInstance & ((config: AxiosRequestConfig) => Promise<unknown>)
;(request as unknown as { get: unknown }).get = ((url: string, config?: AxiosRequestConfig) =>
  instance.get(url, config).then((r) => r)) as never
;(request as unknown as { post: unknown }).post = ((url: string, data?: unknown, config?: AxiosRequestConfig) =>
  instance.post(url, data, config).then((r) => r)) as never
;(request as unknown as { put: unknown }).put = ((url: string, data?: unknown, config?: AxiosRequestConfig) =>
  instance.put(url, data, config).then((r) => r)) as never
;(request as unknown as { delete: unknown }).delete = ((url: string, config?: AxiosRequestConfig) =>
  instance.delete(url, config).then((r) => r)) as never

export async function refreshToken(): Promise<boolean> {
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!refresh) return false
  try {
    const res = await axios.post<{ access_token: string }>('/api/auth/refresh', {
      refresh_token: refresh,
    })
    localStorage.setItem(TOKEN_KEY, res.data.access_token)
    return true
  } catch {
    clearTokens()
    return false
  }
}

export default request

export type { AxiosResponse }