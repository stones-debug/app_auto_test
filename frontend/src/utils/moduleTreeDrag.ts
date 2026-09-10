import type { TestModule } from '@/api/modules'

import { collectSubtreeIds, siblingsOf } from './moduleTree'

export type DropPosition = 'before' | 'after' | 'inside'

export interface DropTarget {
  moduleId: number
  position: DropPosition
}

/** 服务端 `/modules/{id}/position` 的请求体。 */
export interface MovePlan {
  moduleId: number
  parentId: number | null
  beforeId: number | null
}

/**
 * 指针在目标行内的相对位置 → 落点。
 *
 * 上 25% 落在目标之前、下 25% 落在目标之后（同级重排），中间 50% 落进目标内部
 * （成为子模块）。行高由调用方从实际 DOM 量取，避免硬编码。
 */
export function resolveDropPosition(offsetY: number, rowHeight: number): DropPosition {
  const height = rowHeight > 0 ? rowHeight : 1
  const ratio = offsetY / height
  if (ratio < 0.25) return 'before'
  if (ratio > 0.75) return 'after'
  return 'inside'
}

/**
 * 计算拖拽结果；返回 `null` 表示落点非法或结果与当前位置相同。
 *
 * 非法的情况：
 * - 拖到自身；
 * - 落在自己的子树内（含“拖到自己的子模块前面”——那会让模块成为自己的父级）；
 * - `before_id` 指向的模块不存在或不属于目标父级。
 */
export function planModuleMove(
  modules: readonly TestModule[],
  draggedId: number,
  target: DropTarget,
): MovePlan | null {
  const dragged = modules.find((module) => module.id === draggedId)
  const targetNode = modules.find((module) => module.id === target.moduleId)
  if (!dragged || !targetNode || draggedId === target.moduleId) return null

  const subtree = collectSubtreeIds(modules, draggedId)
  const parentId =
    target.position === 'inside' ? target.moduleId : (targetNode.parent_id ?? null)
  // parentId 在子树内 = 把自己挂到自己（或自己的子孙）下
  if (parentId !== null && subtree.has(parentId)) return null

  return resolvePlan(modules, dragged, parentId, planBeforeId(modules, dragged, target, parentId))
}

/** 拖到树底部「根层级」落区：成为顶级模块并排在最后。 */
export function planRootMove(modules: readonly TestModule[], draggedId: number): MovePlan | null {
  const dragged = modules.find((module) => module.id === draggedId)
  if (!dragged) return null
  return resolvePlan(modules, dragged, null, null)
}

function planBeforeId(
  modules: readonly TestModule[],
  dragged: TestModule,
  target: DropTarget,
  parentId: number | null,
): number | null {
  // inside：追加到目标子级末尾；after：插到目标的后一个兄弟之前（已是最后一个则为 null）
  if (target.position === 'inside') return null
  if (target.position === 'before') return target.moduleId

  const siblings = siblingsOf(modules, parentId).filter((module) => module.id !== dragged.id)
  const index = siblings.findIndex((module) => module.id === target.moduleId)
  const next = index >= 0 ? siblings[index + 1] : undefined
  return next ? next.id : null
}

/** 落点与当前位置一致时返回 null，避免无意义的请求与列表抖动。 */
function resolvePlan(
  modules: readonly TestModule[],
  dragged: TestModule,
  parentId: number | null,
  beforeId: number | null,
): MovePlan | null {
  const currentParentId = dragged.parent_id ?? null
  if (currentParentId === parentId) {
    const currentSiblings = siblingsOf(modules, currentParentId)
    const currentIndex = currentSiblings.findIndex((module) => module.id === dragged.id)
    const without = currentSiblings.filter((module) => module.id !== dragged.id)
    const targetIndex =
      beforeId === null ? without.length : without.findIndex((module) => module.id === beforeId)
    if (targetIndex === -1) return null
    if (targetIndex === currentIndex) return null
  }
  return { moduleId: dragged.id, parentId, beforeId }
}
