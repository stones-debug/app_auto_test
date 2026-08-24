import type { Assertion, Step } from '@/api/cases'

export interface CaseEditorSummary {
  setup: number
  main: number
  teardown: number
  assertions: number
  variables: number
}

export function buildCaseEditorSummary(
  steps: Step[],
  assertions: Assertion[],
  variables: Array<{ key: string }>,
): CaseEditorSummary {
  return {
    setup: steps.filter((step) => (step.phase ?? 'main') === 'setup').length,
    main: steps.filter((step) => (step.phase ?? 'main') === 'main').length,
    teardown: steps.filter((step) => (step.phase ?? 'main') === 'teardown').length,
    assertions: assertions.length,
    variables: variables.filter((entry) => entry.key.trim().length > 0).length,
  }
}

export function caseEditorSummaryText(
  moduleName: string,
  summary: CaseEditorSummary,
): string {
  return [
    `模块：${moduleName}`,
    `前置 ${summary.setup}`,
    `主体 ${summary.main}`,
    `断言 ${summary.assertions}`,
    `后置 ${summary.teardown}`,
    `变量 ${summary.variables}`,
  ].join(' · ')
}
