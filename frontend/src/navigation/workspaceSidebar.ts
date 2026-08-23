import type { RouteLocationRaw } from 'vue-router'

export interface WorkspaceMenuItem {
  key: string
  name: string
  to: RouteLocationRaw
  icon: string
  dividerBefore?: boolean
}

export function globalWorkspaceMenus(): WorkspaceMenuItem[] {
  return [
    { key: 'dashboard', name: '工作台', to: { name: 'Dashboard' }, icon: 'Odometer' },
    { key: 'projects', name: '项目', to: { name: 'Projects' }, icon: 'FolderOpened' },
    { key: 'elements', name: '元素库', to: { name: 'Elements' }, icon: 'Grid' },
    { key: 'executions', name: '执行中心', to: { name: 'Executions' }, icon: 'VideoPlay' },
    { key: 'devices', name: '设备中心', to: { name: 'Devices' }, icon: 'Monitor' },
    { key: 'reports', name: '报告', to: { name: 'Reports' }, icon: 'TrendCharts' },
  ]
}

export function projectWorkspaceMenus(projectId: number): WorkspaceMenuItem[] {
  const params = { projectId }
  return [
    { key: 'overview', name: '概览', to: { name: 'ProjectOverview', params }, icon: 'DataBoard' },
    { key: 'cases', name: '用例', to: { name: 'Cases', params }, icon: 'Document' },
    { key: 'suites', name: '套件', to: { name: 'Suites', params }, icon: 'Files' },
    { key: 'elements', name: '元素库', to: { name: 'ProjectElements', params }, icon: 'Grid' },
    { key: 'variables', name: '变量', to: { name: 'Variables', params }, icon: 'Coin' },
    {
      key: 'executions',
      name: '执行',
      to: { name: 'ProjectExecutions', params },
      icon: 'VideoPlay',
      dividerBefore: true,
    },
    { key: 'reports', name: '报告', to: { name: 'ProjectReports', params }, icon: 'TrendCharts' },
    {
      key: 'settings',
      name: '设置',
      to: { name: 'ProjectSettings', params },
      icon: 'Setting',
      dividerBefore: true,
    },
  ]
}
