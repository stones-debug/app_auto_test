<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

// V2 §2.2：AppShell —— 侧栏(全局导航+项目上下文) + 顶栏(面包屑/用户菜单) + 内容区。
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const collapsed = ref(false)
const projectId = computed(() => (route.params.projectId ? Number(route.params.projectId) : null))

const globalMenus = [
  { name: '工作台', path: '/dashboard', icon: 'Odometer' },
  { name: '项目', path: '/projects', icon: 'FolderOpened' },
  { name: '执行中心', path: '/executions', icon: 'VideoPlay' },
  { name: '设备中心', path: '/devices', icon: 'Monitor' },
  { name: '报告', path: '/reports', icon: 'TrendCharts' },
]

const projectMenus = computed(() => {
  if (projectId.value === null) return []
  const p = projectId.value
  return [
    { name: '概览', path: `/projects/${p}/overview`, icon: 'DataBoard' },
    { name: '用例', path: `/projects/${p}/cases`, icon: 'Document' },
    { name: '套件', path: `/projects/${p}/suites`, icon: 'Files' },
    { name: '元素', path: `/projects/${p}/elements`, icon: 'Grid' },
    { name: '变量', path: `/projects/${p}/variables`, icon: 'Coin' },
    { name: '设置', path: `/projects/${p}/settings`, icon: 'Setting' },
  ]
})

const displayName = computed(() => auth.user?.username ?? '未登录')
const avatarText = computed(() => (displayName.value ? displayName.value[0].toUpperCase() : '?'))

function isActive(path: string) {
  if (path === '/dashboard') return route.path === '/dashboard'
  if (path === '/projects') return route.path.startsWith('/projects')
  return route.path === path || route.path.startsWith(`${path}/`)
}
</script>

<template>
  <el-container class="shell">
    <el-aside :width="collapsed ? '72px' : '232px'" class="sidebar">
      <div class="logo" :class="{ collapsed }">
        {{ collapsed ? 'ⓐ' : 'APP 自动化测试平台' }}
      </div>
      <div class="menu-area">
        <div
          v-for="m in globalMenus"
          :key="m.path"
          class="menu-item"
          :class="{ active: isActive(m.path) }"
          :title="collapsed ? m.name : undefined"
          @click="router.push(m.path)"
        >
          <el-icon class="menu-icon"><component :is="m.icon" /></el-icon>
          <span class="menu-text">{{ collapsed ? m.name.slice(0, 1) : m.name }}</span>
        </div>
        <template v-if="projectMenus.length">
          <div class="menu-divider" />
          <div
            v-for="m in projectMenus"
            :key="m.path"
            class="menu-item project"
            :class="{ active: isActive(m.path) }"
            :title="collapsed ? m.name : undefined"
            @click="router.push(m.path)"
          >
            <el-icon class="menu-icon"><component :is="m.icon" /></el-icon>
            <span class="menu-text">{{ collapsed ? '·' : m.name }}</span>
          </div>
        </template>
      </div>
      <div class="sidebar-footer">
        <div class="avatar">{{ avatarText }}</div>
        <div v-if="!collapsed" class="user-info">
          <div class="user-name">{{ displayName }}</div>
          <div class="user-role">{{ auth.user?.is_admin ? '平台管理员' : '用户' }}</div>
        </div>
        <el-icon class="collapse-btn" @click="collapsed = !collapsed">
          <svg viewBox="0 0 1024 1024" width="14" height="14"><path d="M338 512l238-238 45 45-193 193 193 193-45 45z" /></svg>
        </el-icon>
      </div>
    </el-aside>

    <el-container class="main-area">
      <el-header class="header">
        <span class="page-title">{{ (route.meta.title as string) ?? '' }}</span>
        <div class="header-right">
          <el-dropdown @command="auth.logout">
            <span class="header-avatar">{{ avatarText }}</span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.shell {
  height: 100vh;
}
.sidebar {
  background: var(--sidebar-bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.2s;
}
.logo {
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
  white-space: nowrap;
  overflow: hidden;
}
.logo.collapsed {
  font-size: 18px;
}
.menu-area {
  flex: 1;
  padding: 8px;
  overflow-y: auto;
}
.menu-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.08);
  margin: 8px 12px;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: #94a3b8;
  font-size: 14px;
  cursor: pointer;
  margin-bottom: 2px;
  transition: all 0.15s;
}
.menu-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #cbd5e1;
}
.menu-item.active {
  background: rgba(79, 70, 229, 0.35);
  color: #fff;
}
.menu-item.project.active {
  background: rgba(16, 185, 129, 0.25);
}
.menu-icon {
  font-size: 16px;
  flex-shrink: 0;
}
.menu-text {
  white-space: nowrap;
  overflow: hidden;
}
.sidebar-footer {
  padding: 10px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.avatar {
  width: 32px;
  height: 32px;
  background: linear-gradient(135deg, #4f46e5, #818cf8);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}
.user-info {
  flex: 1;
  min-width: 0;
}
.user-name {
  color: #e2e8f0;
  font-size: 13px;
  font-weight: 500;
}
.user-role {
  color: #64748b;
  font-size: 11px;
}
.collapse-btn {
  color: #94a3b8;
  cursor: pointer;
}
.main-area {
  flex-direction: column;
}
.header {
  height: var(--header-height);
  background: #fff;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 18px;
}
.header-avatar {
  width: 30px;
  height: 30px;
  background: linear-gradient(135deg, #4f46e5, #818cf8);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.content {
  padding: var(--content-padding);
  overflow: auto;
  background: var(--bg);
}
</style>
