import type { Step } from '@/api/cases'

export type SuiteStepPayload = Omit<Step, 'assertions'>

/** 套件前后置步骤只提交动作字段，避免把通用编辑器的断言元数据发给后端。 */
export function toSuiteStepPayload(steps: Step[]): SuiteStepPayload[] {
  return steps.map(({ assertions: _assertions, ...step }) => step)
}
