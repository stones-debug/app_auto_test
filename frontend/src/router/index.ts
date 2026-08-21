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

const WRITE_META = new Set(['CaseNew', 'CaseEdit'])

router.beforeEach((to) => {
  const authed = !!getToken()
  if (!to.meta.public && !authed) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  // viewer 写页守卫：后端仍为最终防线；此处按项目角色二次校验由 F4 usePermission 负责，
  // 路由层先只做登录态兜底，403 由后端响应跳转处理。
  if (authed && to.name && WRITE_META.has(String(to.name)) && to.meta.roleRequired) {
    // 预留：F6 接入 projectContextStore 后按角色跳 /403
  }
  if (authed && to.name === 'Login') {
    return { name: 'Dashboard' }
  }
  return true
})

export default router