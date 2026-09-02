import type { FlowNode, StepPhase } from '@/api/cases'

export const FLOW_PHASES: readonly StepPhase[] = ['setup', 'main', 'teardown']

function phaseOf(node: FlowNode): StepPhase {
  return node.phase ?? 'main'
}

function phaseRank(phase: StepPhase): number {
  return FLOW_PHASES.indexOf(phase)
}

/**
 * 将某个阶段的编辑结果合并回完整节点列表。
 *
 * 节点可能通过 vuedraggable 从其他阶段拖入，因此不能只按 phase 过滤，
 * 还要按稳定 key/对象引用从原列表移除，避免同一个节点保留两份。
 * 只有目标阶段重新编号，其他阶段的 order 保持不变。
 */
export function mergeFlowNodes(
  allNodes: FlowNode[],
  phase: StepPhase,
  targetNodes: FlowNode[],
): FlowNode[] {
  const targetKeys = new Set(targetNodes.map((node) => node.key).filter((key): key is string => Boolean(key)))
  const targetRefs = new Set(targetNodes)
  const others = allNodes.filter((node) => {
    // 父组件回写时 targetNodes 通常是复制后的对象，不能仅依赖引用判断。
    // 目标阶段的旧节点必须整体移除；跨阶段拖入的节点则按 key 移除旧副本。
    if (phaseOf(node) === phase) return false
    if (targetRefs.has(node)) return false
    return !node.key || !targetKeys.has(node.key)
  })
  const updatedTarget = targetNodes.map((node, index) => ({
    ...node,
    phase,
    order: index + 1,
  }))

  return [...others, ...updatedTarget].sort((left, right) => {
    const phaseDifference = phaseRank(phaseOf(left)) - phaseRank(phaseOf(right))
    if (phaseDifference !== 0) return phaseDifference
    return left.order - right.order
  })
}

/**
 * 保存前将节点按阶段优先级排列，并在每个阶段内从 1 开始连续编号。
 */
export function normalizeFlowNodeOrders(nodes: FlowNode[]): FlowNode[] {
  return FLOW_PHASES.flatMap((phase) => {
    const phaseNodes = nodes
      .filter((node) => phaseOf(node) === phase)
      .sort((left, right) => left.order - right.order)
    return phaseNodes.map((node, index) => ({ ...node, phase, order: index + 1 }))
  })
}
