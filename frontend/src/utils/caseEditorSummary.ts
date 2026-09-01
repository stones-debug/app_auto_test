import type { FlowNode, Step } from '@/api/cases'

export interface CaseEditorSummary {
  setup: number
  main: number
  teardown: number
  assertions: number
  variables: number
}

export function buildCaseEditorSummary(
  steps: Array<FlowNode | Step>,
  variablesOrAssertions: Array<{ key?: string; order?: number; type?: string }>,
  legacyVariables?: Array<{ key?: string; order?: number; type?: string }>,
): CaseEditorSummary {
  const variables = legacyVariables ?? variablesOrAssertions as Array<{ key: string }>
  const flatAssertions = steps.filter((node) => 'kind' in node && node.kind === 'assertion')
  const nestedAssertions = steps.reduce(
    (total, step) => total + (('assertions' in step ? step.assertions?.length : 0) ?? 0),
    0,
  )
  // 旧调用方把 assertions 单独传入；V3 调用方只传 flow_nodes 和变量。
  const legacyAssertions = legacyVariables !== undefined ? variablesOrAssertions.length : 0
  return {
    setup: steps.filter((step) => (step.phase ?? 'main') === 'setup' && (!('kind' in step) || step.kind !== 'assertion')).length,
    main: steps.filter((step) => (step.phase ?? 'main') === 'main' && (!('kind' in step) || step.kind !== 'assertion')).length,
    teardown: steps.filter((step) => (step.phase ?? 'main') === 'teardown' && (!('kind' in step) || step.kind !== 'assertion')).length,
    assertions: flatAssertions.length || nestedAssertions || legacyAssertions,
    variables: variables.filter((entry) => (entry.key ?? '').trim().length > 0).length,
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
