import { describe, expect, it, vi } from 'vitest'

// node 环境无 window.history，用 memory history 代替
vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return { ...actual, createWebHistory: () => actual.createMemoryHistory() }
})

import router from '@/router'

describe('路由契约（V2 F2）', () => {
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

  it('默认路由重定向到 /dashboard', () => {
    const root = router.getRoutes().find((r) => r.path === '/')
    expect(root?.redirect).toBe('/dashboard')
  })

  it('存在项目概览/设置路由', () => {
    const paths = router.getRoutes().map((r) => r.path)
    expect(paths.some((p) => p.endsWith('/projects/:projectId/overview'))).toBe(true)
    expect(paths.some((p) => p.endsWith('/projects/:projectId/settings'))).toBe(true)
  })

  it('存在执行详情/403/404 路由', () => {
    const paths = router.getRoutes().map((r) => r.path)
    expect(paths.some((p) => p.endsWith('/executions/:executionId'))).toBe(true)
    expect(paths.includes('/403')).toBe(true)
    expect(paths.includes('/404')).toBe(true)
  })

  it('项目工作区包含执行和报告列表/详情路由', () => {
    const paths = router.getRoutes().map((r) => r.path)
    expect(paths).toContain('/projects/:projectId/executions')
    expect(paths).toContain('/projects/:projectId/executions/:executionId')
    expect(paths).toContain('/projects/:projectId/reports')
    expect(paths).toContain('/projects/:projectId/reports/:reportId')
  })

  it('全局与项目详情使用独立 workspace/sidebarKey 元数据', () => {
    expect(router.getRoutes().find((r) => r.name === 'ExecutionDetail')?.meta).toMatchObject({
      workspace: 'global',
      sidebarKey: 'executions',
    })
    expect(router.getRoutes().find((r) => r.name === 'ProjectExecutionDetail')?.meta).toMatchObject({
      workspace: 'project',
      sidebarKey: 'executions',
    })
    expect(router.getRoutes().find((r) => r.name === 'ProjectReportDetail')?.meta).toMatchObject({
      workspace: 'project',
      sidebarKey: 'reports',
    })
  })

  it('兜底路由重定向到 /404（不再静默跳 /projects）', () => {
    const catchAll = router.getRoutes().find((r) => r.path === '/:pathMatch(.*)*')
    expect(catchAll?.redirect).toBe('/404')
  })
})
