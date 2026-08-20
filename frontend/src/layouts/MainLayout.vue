<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getProject } from '@/api/projects'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const projectId = computed(() => (route.params.projectId ? Number(route.params.projectId) : null))
const inProject = computed(() => projectId.value !== null)
const projectName = ref('')

const projectMenus = computed(() => [
  { name: '用例管理', path: `/projects/${projectId.value}/cases` },
  { name: '元素管理', path: `/projects/${projectId.value}/elements` },
  { name: '套件管理', path: `/projects/${projectId.value}/suites` },
  { name: '变量管理', path: `/projects/${projectId.value}/variables` },
])

const globalMenus = [
  { name: '项目管理', path: '/projects' },
  { name: '设备管理', path: '/devices' },
  { name: '执行记录', path: '/executions' },
  { name: '报告管理', path: '/reports' },
]

watch(
  projectId,
  async (id) => {
    projectName.value = ''
    if (id) {
      try {
        const project = await getProject(id)
        projectName.value = project.name
      } catch {
        projectName.value = ''
      }
    }
  },
  { immediate: true },
)

function isProjectActive(path: string) {
  return route.path.startsWith(path)
}

function isGlobalActive(path: string) {
  return route.path === path
}

const displayName = computed(() => auth.user?.username ?? '未登录')
const displayRole = computed(() => (auth.user?.username === 'admin' ? '管理员' : '用户'))
const avatarText = computed(() => (displayName.value ? displayName.value[0].toUpperCase() : '?'))
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">APP 自动化测试平台</div>

      <template v-if="inProject">
        <div class="project-nav">
          <div class="back-btn" @click="router.push('/projects')">← 返回项目列表</div>
          <div class="project-name">{{ projectName || '项目' }}</div>
        </div>
        <div class="menu-group">
          <div
            v-for="item in projectMenus"
            :key="item.name"
            class="menu-item"
            :class="{ active: isProjectActive(item.path) }"
            @click="router.push(item.path)"
          >
            {{ item.name }}
          </div>
        </div>
        <div class="menu-divider"></div>
      </template>

      <div class="menu-group global">
        <div
          v-for="item in globalMenus"
          :key="item.name"
          class="menu-item"
          :class="{ active: isGlobalActive(item.path) }"
          @click="router.push(item.path)"
        >
          {{ item.name }}
        </div>
      </div>

      <div class="sidebar-footer">
        <div class="avatar">{{ avatarText }}</div>
        <div class="user-info">
          <div class="user-name">{{ displayName }}</div>
          <div class="user-role">{{ displayRole }}</div>
        </div>
      </div>
    </el-aside>

    <el-container class="main-area">
      <el-header class="header">
        <span class="page-title">{{ route.meta.title ?? '' }}</span>
        <div class="header-right">
          <span class="notice">通知</span>
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
.layout {
  height: 100vh;
}
.sidebar {
  background: var(--sidebar-bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
}
.project-nav {
  padding: 12px 16px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}
.back-btn {
  font-size: 12px;
  color: #60a5fa;
  cursor: pointer;
  margin-bottom: 8px;
}
.back-btn:hover {
  color: #93c5fd;
}
.project-name {
  font-size: 14px;
  color: #e2e8f0;
  font-weight: 600;
  padding: 6px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.menu-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.08);
  margin: 4px 16px;
  flex-shrink: 0;
}
.menu-group {
  flex: 1;
  padding: 8px;
  overflow-y: auto;
}
.menu-group.global {
  flex: 0 0 auto;
}
.menu-item {
  padding: 10px 16px;
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
.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  gap: 10px;
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
}
.user-info {
  flex: 1;
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

.main-area {
  flex-direction: column;
}
.header {
  height: 64px;
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
.notice {
  color: var(--text-2);
  font-size: 14px;
  cursor: pointer;
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
  padding: 24px;
  overflow: auto;
  background: var(--bg);
}
</style>
