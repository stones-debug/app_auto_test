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
        <el-icon class="logo-icon"><FolderOpened /></el-icon>
        <span v-if="!collapsed" class="logo-text">APP 自动化测试平台</span>
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
          <span v-if="!collapsed" class="menu-text">{{ m.name }}</span>
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
            <span v-if="!collapsed" class="menu-text">{{ m.name }}</span>
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
          <svg v-if="collapsed" viewBox="0 0 1024 1024" width="14" height="14"><path d="M686 512l-238 238-45-45 193-193-193-193 45-45z" /></svg>
          <svg v-else viewBox="0 0 1024 1024" width="14" height="14"><path d="M338 512l238-238 45 45-193 193 193 193-45 45z" /></svg>
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
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.2s;
}
.logo {
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  padding: 0 16px;
  color: var(--primary);
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid var(--sidebar-border);
  flex-shrink: 0;
  white-space: nowrap;
  overflow: hidden;
}
.logo.collapsed {
  justify-content: center;
  padding: 0;
}
.logo-icon {
  font-size: 20px;
  flex-shrink: 0;
}
.logo-text {
  overflow: hidden;
  text-overflow: ellipsis;
}
.menu-area {
  flex: 1;
  padding: 8px;
  overflow-y: auto;
}
.menu-divider {
  height: 1px;
  background: var(--sidebar-border);
  margin: 8px 12px;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: var(--sidebar-text);
  font-size: 14px;
  cursor: pointer;
  margin-bottom: 2px;
  transition: all 0.15s;
}
.menu-item:hover {
  background: #f1f5f9;
  color: var(--text);
}
.menu-item.active {
  background: var(--primary-light);
  color: var(--sidebar-text-active);
  font-weight: 600;
}
.menu-item.project.active {
  background: #ecfdf5;
  color: #059669;
}
.menu-icon {
  font-size: 16px;
  flex-shrink: 0;
}
.sidebar-footer {
  padding: 10px 12px;
  border-top: 1px solid var(--sidebar-border);
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
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
}
.user-role {
  color: var(--text-2);
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
