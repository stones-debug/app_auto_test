import { describe, expect, it } from 'vitest'

import { globalWorkspaceMenus, projectWorkspaceMenus } from '@/navigation/workspaceSidebar'

describe('AppShell 工作区侧栏契约', () => {
  it('全局侧栏只展示全局入口', () => {
    expect(globalWorkspaceMenus().map((item) => item.key)).toEqual([
      'dashboard', 'projects', 'executions', 'devices', 'reports',
    ])
  })

  it('项目侧栏替换全局菜单，并包含项目执行和报告', () => {
    const menus = projectWorkspaceMenus(12)
    expect(menus.map((item) => item.key)).toEqual([
      'overview', 'cases', 'suites', 'elements', 'variables', 'executions', 'reports', 'settings',
    ])
    expect(menus.find((item) => item.key === 'executions')?.to).toEqual({
      name: 'ProjectExecutions',
      params: { projectId: 12 },
    })
    expect(menus.find((item) => item.key === 'reports')?.to).toEqual({
      name: 'ProjectReports',
      params: { projectId: 12 },
    })
  })
})
