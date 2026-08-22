import { computed } from 'vue'

import { useProjectContextStore } from '@/stores/projectContext'

// V2 §6.3：权限能力判断（统一走 projectContextStore 计算，不在页面散落角色字符串）。
// 注意：ctx.xxx 经 Pinia setup store 自动解包为布尔值（非 computed），
// 必须包一层 computed 保持响应式，否则首次渲染后权限变化不更新。
export function usePermission() {
  const ctx = useProjectContextStore()
  return {
    role: computed(() => ctx.role),
    canReadProject: computed(() => ctx.canReadProject),
    canWriteAssets: computed(() => ctx.canWriteAssets),
    canExecute: computed(() => ctx.canExecute),
    canEditProject: computed(() => ctx.canEditProject),
    canDeleteProject: computed(() => ctx.canDeleteProject),
    canManageAdmins: computed(() => ctx.canManageAdmins),
    canManageMembers: computed(() => ctx.canManageMembers),
    isPlatformAdmin: computed(() => ctx.isPlatformAdmin),
  }
}