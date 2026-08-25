import type { Step, StepPhase } from '@/api/cases'

/**
 * 跨相位拖拽/内部排序后的步骤重组：将指定 phase 的步骤序列合并回全量 steps，
 * 其余 phase 的步骤保持原有顺序，目标 phase 内重新按序编号。
 * 返回新数组，但直接复用传入步骤对象（保持引用身份，用于折叠状态对应）。
 * 供受 vuedraggable group 拖拽驱动的 phaseSteps setter 使用。
 */
export function mergePhaseSteps(
  allSteps: Step[],
  phase: StepPhase,
  targetPhaseSteps: Step[],
): Step[] {
  const others = allSteps.filter((step) => (step.phase ?? 'main') !== phase)
  targetPhaseSteps.forEach((step, index) => {
    step.phase = phase
    step.order = index + 1
  })
  return [...others, ...targetPhaseSteps]
}
