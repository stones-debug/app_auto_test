<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { globalWorkspaceMenus, projectWorkspaceMenus } from '@/navigation/workspaceSidebar'
import { useAuthStore } from '@/stores/auth'
import { useLayoutStore } from '@/stores/layout'
import { useProjectContextStore } from '@/stores/projectContext'

// V2 §2.2：AppShell —— 侧栏(全局导航+项目上下文) + 顶栏(面包屑/用户菜单) + 内容区。
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const layout = useLayoutStore()
const projectContext = useProjectContextStore()

const projectId = computed(() => (route.params.projectId ? Number(route.params.projectId) : null))
const isProjectWorkspace = computed(
  () => route.meta.workspace === 'project' && projectId.value !== null,
)
const collapsed = computed(() => layout.collapsed)
const activeKey = computed(() => String(route.meta.sidebarKey ?? ''))

const globalMenus = globalWorkspaceMenus()
const projectMenus = computed(() => (
  projectId.value === null ? [] : projectWorkspaceMenus(projectId.value)
))
const projectName = computed(() => {
  if (projectContext.project?.id === projectId.value) return projectContext.project.name
  return projectContext.loading ? '正在加载项目…' : `项目 #${projectId.value ?? '-'}`
})

const displayName = computed(() => auth.user?.username ?? '未登录')
const avatarText = computed(() => (displayName.value ? displayName.value[0].toUpperCase() : '?'))

function isActive(key: string) {
  return activeKey.value === key
}
</script>

<template>
  <el-container class="shell">
    <el-aside :width="collapsed ? '72px' : '232px'" class="sidebar">
      <div class="logo" :class="{ collapsed }" role="button" tabindex="0" @click="router.push('/dashboard')" @keyup.enter="router.push('/dashboard')">
        <img src="/favicon.svg" alt="" class="logo-mark" />
        <span v-if="!collapsed">APP 自动化测试平台</span>
      </div>
      <div class="menu-area">
        <template v-if="isProjectWorkspace">
          <div class="workspace-back" :title="collapsed ? '全部项目' : undefined" @click="router.push('/projects')">
            <span>{{ collapsed ? '←' : '← 全部项目' }}</span>
          </div>
          <div class="project-identity" :class="{ collapsed }" :title="collapsed ? projectName : undefined">
            <el-icon><FolderOpened /></el-icon>
            <div v-if="!collapsed" class="project-copy">
              <span class="project-name">{{ projectName }}</span>
              <span v-if="projectContext.role" class="project-role">{{ projectContext.role }}</span>
            </div>
          </div>
          <div
            v-for="m in projectMenus"
            :key="m.key"
            class="project-menu-wrap"
          >
            <div v-if="m.dividerBefore" class="menu-divider" />
            <div
              class="menu-item project"
              :class="{ active: isActive(m.key) }"
              :title="collapsed ? m.name : undefined"
              @click="router.push(m.to)"
            >
              <el-icon class="menu-icon"><component :is="m.icon" /></el-icon>
              <span class="menu-text">{{ collapsed ? '·' : m.name }}</span>
            </div>
          </div>
        </template>
        <template v-else>
          <div
            v-for="m in globalMenus"
            :key="m.key"
            class="menu-item"
            :class="{ active: isActive(m.key) }"
            :title="collapsed ? m.name : undefined"
            @click="router.push(m.to)"
          >
            <el-icon class="menu-icon"><component :is="m.icon" /></el-icon>
            <span class="menu-text">{{ collapsed ? m.name.slice(0, 1) : m.name }}</span>
          </div>
        </template>
      </div>
      <div
        v-if="isProjectWorkspace"
        class="global-exit"
        :title="collapsed ? '返回全局工作台' : undefined"
        @click="router.push('/dashboard')"
      >
        <el-icon><Odometer /></el-icon>
        <span v-if="!collapsed">返回全局工作台</span>
      </div>
      <div class="sidebar-footer">
        <div class="avatar">{{ avatarText }}</div>
        <div v-if="!collapsed" class="user-info">
          <div class="user-name">{{ displayName }}</div>
          <div class="user-role">{{ auth.user?.is_admin ? '平台管理员' : '用户' }}</div>
        </div>
        <el-icon class="collapse-btn" @click="layout.toggleCollapsed()">
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
  cursor: pointer;
}
.logo.collapsed {
  font-size: 18px;
}
.logo-mark {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  margin-right: 8px;
  flex-shrink: 0;
}
.logo.collapsed .logo-mark {
  margin-right: 0;
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
.workspace-back {
  min-height: 32px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  margin-bottom: 8px;
  color: #94a3b8;
  font-size: 12px;
  cursor: pointer;
}
.workspace-back:hover {
  color: #fff;
}
.project-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 4px 10px;
  padding: 10px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  color: #e2e8f0;
}
.project-identity.collapsed {
  justify-content: center;
}
.project-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.project-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
}
.project-role {
  color: #94a3b8;
  font-size: 11px;
}
.project-menu-wrap {
  display: contents;
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
.global-exit {
  min-height: 42px;
  padding: 0 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  gap: 10px;
  color: #94a3b8;
  font-size: 13px;
  cursor: pointer;
}
.global-exit:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
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
  scrollbar-gutter: stable;
  background: var(--bg);
}
</style>
