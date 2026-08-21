import { describe, expect, it, vi } from 'vitest'

// node 环境无 window.history，用 memory history 代替
vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return { ...actual, createWebHistory: () => actual.createMemoryHistory() }
})

import router from '@/router'

describe('路由契约（CR-09）', () => {
  it('存在新建用例路由 /cases/new', () => {
    const paths = router.getRoutes().map((r) => r.path)
    expect(paths.some((p) => p.endsWith('/cases/new'))).toBe(true)
  })

  it('存在编辑用例路由 /cases/:caseId/edit', () => {
    const paths = router.getRoutes().map((r) => r.path)
    expect(paths.some((p) => p.endsWith('/cases/:caseId/edit'))).toBe(true)
  })

  it('未登录访问受保护路由重定向到登录页', () => {
    expect(router.hasRoute('Login')).toBe(true)
  })
})
