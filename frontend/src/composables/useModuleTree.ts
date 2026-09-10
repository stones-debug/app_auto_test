import { computed, ref } from 'vue'

import {
  createModule,
  deleteModule,
  listModules,
  moveModule,
  updateModule,
  type ModuleScope,
  type TestModule,
} from '@/api/modules'
import {
  buildModuleTree,
  flattenModuleTree,
  loadCollapsed,
  saveCollapsed,
  type ModuleRow,
} from '@/utils/moduleTree'
import { planModuleMove, planRootMove, type DropTarget, type MovePlan } from '@/utils/moduleTreeDrag'

/**
 * 模块树的加载、折叠与写操作（用例页与套件页共用）。
 *
 * 组件卸载后不保留任何副作用；折叠状态按 `项目 + scope` 存 localStorage。
 */
export function useModuleTree(projectId: number, scope: ModuleScope) {
  const modules = ref<TestModule[]>([])
  const loading = ref(false)
  const collapsed = ref<Set<number>>(loadCollapsed(projectId, scope))

  const tree = computed(() => buildModuleTree(modules.value))
  const rows = computed<ModuleRow[]>(() => flattenModuleTree(tree.value, collapsed.value))

  async function load() {
    loading.value = true
    try {
      modules.value = await listModules(projectId, { scope })
      // 折叠集合里已被删除的模块顺手清掉，避免 key 无限增长
      const alive = new Set(modules.value.map((module) => module.id))
      const pruned = new Set([...collapsed.value].filter((id) => alive.has(id)))
      if (pruned.size !== collapsed.value.size) collapsed.value = pruned
    } finally {
      loading.value = false
    }
  }

  function persistCollapsed() {
    saveCollapsed(projectId, scope, collapsed.value)
  }

  function toggleCollapse(moduleId: number) {
    const next = new Set(collapsed.value)
    if (next.has(moduleId)) next.delete(moduleId)
    else next.add(moduleId)
    collapsed.value = next
    persistCollapsed()
  }

  function expand(moduleId: number | null) {
    if (moduleId == null || !collapsed.value.has(moduleId)) return
    const next = new Set(collapsed.value)
    next.delete(moduleId)
    collapsed.value = next
    persistCollapsed()
  }

  async function create(name: string, parentId: number | null): Promise<TestModule> {
    const created = await createModule(projectId, { name, parent_id: parentId, scope })
    await load()
    expand(parentId)
    return created
  }

  async function rename(moduleId: number, name: string) {
    await updateModule(moduleId, { name })
    await load()
  }

  async function remove(moduleId: number) {
    await deleteModule(moduleId)
    await load()
  }

  function canDrop(draggedId: number, target: DropTarget): boolean {
    return planModuleMove(modules.value, draggedId, target) !== null
  }

  function canDropToRoot(draggedId: number): boolean {
    return planRootMove(modules.value, draggedId) !== null
  }

  /** 落点与当前位置一致时返回 false，调用方据此跳过提示与请求。 */
  async function drop(draggedId: number, target: DropTarget): Promise<boolean> {
    const plan = planModuleMove(modules.value, draggedId, target)
    if (!plan) return false
    await applyPlan(plan)
    return true
  }

  async function dropToRoot(draggedId: number): Promise<boolean> {
    const plan = planRootMove(modules.value, draggedId)
    if (!plan) return false
    await applyPlan(plan)
    return true
  }

  async function applyPlan(plan: MovePlan) {
    // 服务端返回该 scope 的完整列表，直接替换即可，无需本地重排
    modules.value = await moveModule(plan.moduleId, {
      parent_id: plan.parentId,
      before_id: plan.beforeId,
    })
  }

  /** 「移动到…」菜单：挂到目标模块下（追加末尾），`parentId=null` 表示根层级末尾。 */
  async function moveTo(moduleId: number, parentId: number | null): Promise<boolean> {
    if (parentId === null) return dropToRoot(moduleId)
    const plan: MovePlan = { moduleId, parentId, beforeId: null }
    const current = modules.value.find((module) => module.id === moduleId)
    if (current && (current.parent_id ?? null) === parentId && isLastChild(moduleId, parentId)) {
      return false
    }
    await applyPlan(plan)
    return true
  }

  function isLastChild(moduleId: number, parentId: number | null): boolean {
    const siblings = modules.value
      .filter((module) => (module.parent_id ?? null) === parentId)
      .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
    return siblings.length > 0 && siblings[siblings.length - 1].id === moduleId
  }

  return {
    modules,
    rows,
    tree,
    loading,
    collapsed,
    load,
    toggleCollapse,
    expand,
    create,
    rename,
    remove,
    canDrop,
    canDropToRoot,
    drop,
    dropToRoot,
    moveTo,
  }
}
