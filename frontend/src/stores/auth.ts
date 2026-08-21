import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import request, { clearTokens, getToken, setTokens } from '@/utils/request'

export interface UserInfo {
  id: number
  username: string
  email: string
  is_admin?: boolean
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const token = ref<string | null>(getToken())
  const isLoggedIn = computed(() => !!token.value)

  async function login(username: string, password: string): Promise<void> {
    const res = await request.post<{ access_token: string; refresh_token: string; user: UserInfo }>(
      '/auth/login',
      { username, password },
    )
    setTokens(res.access_token, res.refresh_token)
    token.value = res.access_token
    user.value = res.user
  }

  async function fetchMe(): Promise<void> {
    const res = await request.get<UserInfo>('/auth/me')
    user.value = res
  }

  async function logout(): Promise<void> {
    // CR-14：先尽力调用后端撤销 refresh token，再清本地状态
    const refresh = localStorage.getItem('refresh_token')
    if (refresh) {
      try {
        await request.post('/auth/logout', { refresh_token: refresh })
      } catch {
        // 撤销失败不阻塞登出
      }
    }
    clearTokens()
    token.value = null
    user.value = null
    window.location.href = '/login'
  }

  return { user, token, isLoggedIn, login, fetchMe, logout }
})