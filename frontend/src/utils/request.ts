import axios, { type AxiosError, type AxiosRequestConfig } from 'axios'

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