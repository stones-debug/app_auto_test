import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { getProject, type Project } from '@/api/projects'
import { useAuthStore } from '@/stores/auth'

// V2 §6.1 / §6.3：当前项目上下文——项目详情、角色、权限能力计算。
// 路由项目变化时调用 load(projectId)；5 分钟软缓存避免重复请求。
const CACHE_TTL = 5 * 60 * 1000

export const useProjectContextStore = defineStore('projectContext', () => {
  const auth = useAuthStore()
  const project = ref<Project | null>(null)
  const role = ref<string | null>(null)
  const loadedAt = ref(0)
  const loading = ref(false)
  let requestSerial = 0

  const canReadProject = computed(() => ['owner', 'admin', 'member', 'viewer'].includes(role.value ?? ''))
  const canWriteAssets = computed(() => ['owner', 'admin', 'member'].includes(role.value ?? ''))
  const canExecute = computed(() => canWriteAssets.value)
  const canEditProject = computed(() => ['owner', 'admin'].includes(role.value ?? ''))
  const canDeleteProject = computed(() => role.value === 'owner')
  const canManageAdmins = computed(() => role.value === 'owner')
  const canManageMembers = computed(() => ['owner', 'admin'].includes(role.value ?? ''))
  const isPlatformAdmin = computed(() => !!auth.user?.is_admin)

  async function load(projectId: number | null, { force = false } = {}) {
    if (projectId === null) {
      clear()
      return
    }
    const now = Date.now()
    if (!force && project.value?.id === projectId && now - loadedAt.value < CACHE_TTL) {
      return
    }
    const serial = ++requestSerial
    if (project.value?.id !== projectId) {
      project.value = null
      role.value = null
      loadedAt.value = 0
    }
    loading.value = true
    try {
      const data = await getProject(projectId)
      if (serial !== requestSerial) return
      project.value = data
      role.value = data.role ?? null
      loadedAt.value = Date.now()
    } finally {
      if (serial === requestSerial) loading.value = false
    }
  }

  function clear() {
    requestSerial += 1
    project.value = null
    role.value = null
    loadedAt.value = 0
    loading.value = false
  }

  return {
    project,
    role,
    loading,
    canReadProject,
    canWriteAssets,
    canExecute,
    canEditProject,
    canDeleteProject,
    canManageAdmins,
    canManageMembers,
    isPlatformAdmin,
    load,
    clear,
  }
})
