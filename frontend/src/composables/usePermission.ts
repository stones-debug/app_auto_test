import { computed } from 'vue'

import { useProjectContextStore } from '@/stores/projectContext'

// V2 §6.3：权限能力判断（统一走 projectContextStore 计算，不在页面散落角色字符串）。
export function usePermission() {
  const ctx = useProjectContextStore()
  return {
    role: computed(() => ctx.role),
    canReadProject: ctx.canReadProject,
    canWriteAssets: ctx.canWriteAssets,
    canExecute: ctx.canExecute,
    canEditProject: ctx.canEditProject,
    canDeleteProject: ctx.canDeleteProject,
    canManageAdmins: ctx.canManageAdmins,
    canManageMembers: ctx.canManageMembers,
    isPlatformAdmin: ctx.isPlatformAdmin,
  }
}