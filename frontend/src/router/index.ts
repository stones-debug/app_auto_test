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
        meta: { title: '工作台' },
      },
      {
        path: 'projects',
        name: 'Projects',
        component: () => import('@/views/Project.vue'),
        meta: { title: '项目管理' },
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
            meta: { title: '项目概览' },
          },
          {
            path: 'cases',
            name: 'Cases',
            component: () => import('@/views/Case.vue'),
            meta: { title: '测试用例' },
          },
          {
            path: 'cases/new',
            name: 'CaseNew',
            component: () => import('@/views/CaseEditor.vue'),
            meta: { title: '新建用例' },
          },
          {
            path: 'cases/:caseId/edit',
            name: 'CaseEdit',
            component: () => import('@/views/CaseEditor.vue'),
            meta: { title: '用例编辑' },
          },
          {
            path: 'suites',
            name: 'Suites',
            component: () => import('@/views/Suite.vue'),
            meta: { title: '测试套件' },
          },
          {
            path: 'elements',
            name: 'Elements',
            component: () => import('@/views/Element.vue'),
            meta: { title: '元素库' },
          },
          {
            path: 'variables',
            name: 'Variables',
            component: () => import('@/views/Variable.vue'),
            meta: { title: '变量' },
          },
          {
            path: 'settings',
            name: 'ProjectSettings',
            component: () => import('@/views/ProjectSettings.vue'),
            meta: { title: '项目设置' },
          },
        ],
      },
      {
        path: 'executions',
        name: 'Executions',
        component: () => import('@/views/Execution.vue'),
        meta: { title: '执行中心' },
      },
      {
        path: 'executions/:executionId',
        name: 'ExecutionDetail',
        component: () => import('@/views/ExecutionDetail.vue'),
        meta: { title: '执行详情' },
      },
      {
        path: 'devices',
        name: 'Devices',
        component: () => import('@/views/Device.vue'),
        meta: { title: '设备中心' },
      },
      {
        path: 'reports',
        name: 'Reports',
        component: () => import('@/views/Report.vue'),
        meta: { title: '报告中心' },
      },
      {
        path: 'reports/:id',
        name: 'ReportDetail',
        component: () => import('@/views/ReportDetail.vue'),
        meta: { title: '报告详情' },
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