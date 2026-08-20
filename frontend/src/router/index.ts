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
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      { path: '', redirect: '/projects' },
      {
        path: 'projects',
        name: 'Projects',
        component: () => import('@/views/Project.vue'),
        meta: { title: '项目管理' },
      },
      {
        path: 'projects/:projectId/elements',
        name: 'Elements',
        component: () => import('@/views/Element.vue'),
        meta: { title: '元素管理' },
      },
      {
        path: 'projects/:projectId/cases',
        name: 'Cases',
        component: () => import('@/views/Case.vue'),
        meta: { title: '用例管理' },
      },
      {
        path: 'projects/:projectId/suites',
        name: 'Suites',
        component: () => import('@/views/Suite.vue'),
        meta: { title: '套件管理' },
      },
      {
        path: 'projects/:projectId/variables',
        name: 'Variables',
        component: () => import('@/views/Variable.vue'),
        meta: { title: '变量管理' },
      },
      {
        path: 'executions',
        name: 'Executions',
        component: () => import('@/views/Execution.vue'),
        meta: { title: '执行记录' },
      },
      {
        path: 'devices',
        name: 'Devices',
        component: () => import('@/views/Device.vue'),
        meta: { title: '设备管理' },
      },
      {
        path: 'reports',
        name: 'Reports',
        component: () => import('@/views/Report.vue'),
        meta: { title: '报告管理' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/projects' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  if (!to.meta.public && !getToken()) {
    return { name: 'Login' }
  }
  return true
})

export default router