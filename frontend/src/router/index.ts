import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { getToken } from '@/utils/request'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/403',
    name: 'Forbidden',
    component: () => import('@/views/Forbidden.vue'),
    meta: { public: true },
  },
  {
    path: '/404',
    name: 'NotFound',
    component: () => import('@/views/NotFound.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      {
        path: '',
        redirect: '/dashboard',
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '工作台', workspace: 'global', sidebarKey: 'dashboard' },
      },
      {
        path: 'projects',
        name: 'Projects',
        component: () => import('@/views/Project.vue'),
        meta: { title: '项目管理', workspace: 'global', sidebarKey: 'projects' },
      },
      {
        path: 'projects/:projectId',
        component: () => import('@/layouts/ProjectLayout.vue'),
        redirect: (to) => `/projects/${to.params.projectId}/overview`,
        children: [
          {
            path: 'overview',
            name: 'ProjectOverview',
            component: () => import('@/views/ProjectOverview.vue'),
            meta: { title: '项目概览', workspace: 'project', sidebarKey: 'overview' },
          },
          {
            path: 'cases',
            name: 'Cases',
            component: () => import('@/views/Case.vue'),
            meta: { title: '测试用例', workspace: 'project', sidebarKey: 'cases' },
          },
          {
            path: 'cases/new',
            name: 'CaseNew',
            component: () => import('@/views/CaseEditor.vue'),
            meta: { title: '新建用例', workspace: 'project', sidebarKey: 'cases' },
          },
          {
            path: 'cases/:caseId/edit',
            name: 'CaseEdit',
            component: () => import('@/views/CaseEditor.vue'),
            meta: { title: '用例编辑', workspace: 'project', sidebarKey: 'cases' },
          },
          {
            path: 'suites',
            name: 'Suites',
            component: () => import('@/views/Suite.vue'),
            meta: { title: '测试套件', workspace: 'project', sidebarKey: 'suites' },
          },
          {
            path: 'elements',
            name: 'Elements',
            component: () => import('@/views/Element.vue'),
            meta: { title: '元素库', workspace: 'project', sidebarKey: 'elements' },
          },
          {
            path: 'variables',
            name: 'Variables',
            component: () => import('@/views/Variable.vue'),
            meta: { title: '变量', workspace: 'project', sidebarKey: 'variables' },
          },
          {
            path: 'executions',
            name: 'ProjectExecutions',
            component: () => import('@/views/Execution.vue'),
            meta: { title: '项目执行', workspace: 'project', sidebarKey: 'executions' },
          },
          {
            path: 'executions/:executionId',
            name: 'ProjectExecutionDetail',
            component: () => import('@/views/ExecutionDetail.vue'),
            meta: { title: '执行详情', workspace: 'project', sidebarKey: 'executions' },
          },
          {
            path: 'reports',
            name: 'ProjectReports',
            component: () => import('@/views/Report.vue'),
            meta: { title: '项目报告', workspace: 'project', sidebarKey: 'reports' },
          },
          {
            path: 'reports/:reportId',
            name: 'ProjectReportDetail',
            component: () => import('@/views/ReportDetail.vue'),
            meta: { title: '报告详情', workspace: 'project', sidebarKey: 'reports' },
          },
          {
            path: 'settings',
            name: 'ProjectSettings',
            component: () => import('@/views/ProjectSettings.vue'),
            meta: { title: '项目设置', workspace: 'project', sidebarKey: 'settings' },
          },
        ],
      },
      {
        path: 'executions',
        name: 'Executions',
        component: () => import('@/views/Execution.vue'),
        meta: { title: '执行中心', workspace: 'global', sidebarKey: 'executions' },
      },
      {
        path: 'executions/:executionId',
        name: 'ExecutionDetail',
        component: () => import('@/views/ExecutionDetail.vue'),
        meta: { title: '执行详情', workspace: 'global', sidebarKey: 'executions' },
      },
      {
        path: 'devices',
        name: 'Devices',
        component: () => import('@/views/Device.vue'),
        meta: { title: '设备中心', workspace: 'global', sidebarKey: 'devices' },
      },
      {
        path: 'reports',
        name: 'Reports',
        component: () => import('@/views/Report.vue'),
        meta: { title: '报告中心', workspace: 'global', sidebarKey: 'reports' },
      },
      {
        path: 'reports/:id',
        name: 'ReportDetail',
        component: () => import('@/views/ReportDetail.vue'),
        meta: { title: '报告详情', workspace: 'global', sidebarKey: 'reports' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/404' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const authed = !!getToken()
  if (!to.meta.public && !authed) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (authed && to.name === 'Login') {
    return { name: 'Dashboard' }
  }
  // 项目角色级权限（viewer 写页）由后端 403 作为最终防线；前端组件用 PermissionGate/usePermission 控制入口。
  return true
})

export default router
