export type OrderedExecutionItem<TStep, TAssertion> =
  | { kind: 'step'; index: number; value: TStep }
  | { kind: 'assertion'; index: number; value: TAssertion }

interface PhasedStep {
  phase?: string
}

/** 用例的实际执行顺序：前置/主体步骤 → 断言 → 后置步骤。 */
export function orderExecutionItems<TStep extends PhasedStep, TAssertion>(
  steps: TStep[],
  assertions: TAssertion[],
): OrderedExecutionItem<TStep, TAssertion>[] {
  const beforeAssertions: OrderedExecutionItem<TStep, TAssertion>[] = []
  const afterAssertions: OrderedExecutionItem<TStep, TAssertion>[] = []

  steps.forEach((step, index) => {
    const item = { kind: 'step' as const, index, value: step }
    if (step.phase === 'teardown') afterAssertions.push(item)
    else beforeAssertions.push(item)
  })

  return [
    ...beforeAssertions,
    ...assertions.map((assertion, index) => ({
      kind: 'assertion' as const,
      index,
      value: assertion,
    })),
    ...afterAssertions,
  ]
}

export function splitExecutionSteps<TStep extends PhasedStep>(steps: TStep[]): {
  beforeAssertions: TStep[]
  afterAssertions: TStep[]
} {
  return {
    beforeAssertions: steps.filter((step) => step.phase !== 'teardown'),
    afterAssertions: steps.filter((step) => step.phase === 'teardown'),
  }
}

export function assertionPassed(status: unknown): boolean {
  return ['pass', 'passed'].includes(String(status).toLowerCase())
}
