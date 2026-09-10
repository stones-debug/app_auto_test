import type { TestModule } from '@/api/modules'

/** 树节点：模块 + 子节点。 */
export type ModuleNode = TestModule & { children: ModuleNode[] }

export interface ModuleRow {
  node: ModuleNode
  level: number
}

/**
 * 扁平模块列表 → 树。
 *
 * 父节点缺失（已被删除、或跨项目脏数据）的模块会**提升为根**，而不是从界面上消失；
 * 同级按 `(sort_order, id)` 稳定排序，与后端 `list_modules` 的排序口径一致。
 */
export function buildModuleTree(modules: readonly TestModule[]): ModuleNode[] {
  const nodes = new Map<number, ModuleNode>()
  for (const module of modules) nodes.set(module.id, { ...module, children: [] })

  const roots: ModuleNode[] = []
  for (const node of nodes.values()) {
    const parent = node.parent_id != null ? nodes.get(node.parent_id) : undefined
    if (parent && parent.id !== node.id) parent.children.push(node)
    else roots.push(node)
  }

  const sortLevel = (level: ModuleNode[]): void => {
    level.sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
    for (const node of level) sortLevel(node.children)
  }
  sortLevel(roots)
  return roots
}

/** 树 → 可见行（跳过折叠节点的子树），带缩进层级。 */
export function flattenModuleTree(
  tree: readonly ModuleNode[],
  collapsed: ReadonlySet<number>,
  level = 0,
): ModuleRow[] {
  return tree.flatMap((node) => [
    { node, level },
    ...(collapsed.has(node.id) ? [] : flattenModuleTree(node.children, collapsed, level + 1)),
  ])
}

/**
 * 模块自身及其全部子孙 id。
 *
 * 已访问集合同时起到防环作用：即使历史数据里出现环（A 的父亲是 B、B 的父亲是 A），
 * 也不会死循环。
 */
export function collectSubtreeIds(
  modules: readonly TestModule[],
  moduleId: number,
): Set<number> {
  const children = new Map<number | null, number[]>()
  for (const module of modules) {
    const key = module.parent_id ?? null
    const bucket = children.get(key)
    if (bucket) bucket.push(module.id)
    else children.set(key, [module.id])
  }
  const result = new Set<number>()
  const pending: number[] = [moduleId]
  while (pending.length) {
    const current = pending.pop() as number
    if (result.has(current)) continue
    result.add(current)
    pending.push(...(children.get(current) ?? []))
  }
  return result
}

/** 同一父级下的兄弟（含自身），按 `(sort_order, id)` 排序。 */
export function siblingsOf(
  modules: readonly TestModule[],
  parentId: number | null,
): TestModule[] {
  return modules
    .filter((module) => (module.parent_id ?? null) === parentId)
    .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
}

/** 折叠状态在 localStorage 的存储键（按项目 + scope 隔离）。 */
export function collapsedStorageKey(projectId: number, scope: string): string {
  return `module-tree:${projectId}:${scope}`
}

export function loadCollapsed(projectId: number, scope: string): Set<number> {
  try {
    const raw = localStorage.getItem(collapsedStorageKey(projectId, scope))
    if (!raw) return new Set()
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((item): item is number => Number.isInteger(item)))
  } catch {
    // 存储不可用或数据损坏时静默回落到全部展开
    return new Set()
  }
}

export function saveCollapsed(projectId: number, scope: string, collapsed: ReadonlySet<number>): void {
  try {
    localStorage.setItem(collapsedStorageKey(projectId, scope), JSON.stringify([...collapsed]))
  } catch {
    // 忽略配额/隐私模式下的写入失败：折叠状态只是体验优化
  }
}
