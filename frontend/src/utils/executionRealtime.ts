import type { ExecutionStatus } from '@/api/executions'

type RealtimeMessage = Record<string, unknown>

interface RealtimeStep {
  id?: number
  step_order: number
  status: string
  duration?: number | null
  actual_value?: string | null
  error_message?: string | null
  artifact_id?: number | null
  phase?: string | null
}

interface RealtimeAssertion {
  id?: number
  assertion_order?: number | null
  assertion_type: string
  expected_value?: string | null
  actual_value?: string | null
  status: string
  error_message?: string | null
  params?: Record<string, unknown> | null
  description?: string | null
}

interface RealtimeCase {
  id?: number
  case_id: number
  status: string
  duration?: number | null
  error_message?: string | null
  steps?: RealtimeStep[]
  assertions?: RealtimeAssertion[]
}

function findSuite(suites: RealtimeSuite[], message: RealtimeMessage): RealtimeSuite | null {
  const executionSuiteId = message.execution_suite_id
  if (executionSuiteId != null) {
    const numeric = Number(executionSuiteId)
    return suites.find((suite) => suite.id != null && suite.id === numeric) ?? null
  }
  const legacySuiteId = message.suite_id
  if (legacySuiteId == null) return null
  const numeric = Number(legacySuiteId)
  return suites.find((suite) => suite.suite_id === numeric) ?? null
}

interface RealtimeSuite {
  id?: number
  suite_id: number | null
  status: string
  duration?: number | null
  error_message?: string | null
  setup_steps?: RealtimeStep[]
  cases: RealtimeCase[]
  teardown_steps?: RealtimeStep[]
}

export function executionConnectionState(
  terminal: boolean,
  connected: boolean,
  connecting: boolean,
): { kind: 'done' | 'ok' | 'pending' | 'down'; label: string } {
  if (terminal) return { kind: 'done', label: '执行已结束' }
  if (connected) return { kind: 'ok', label: '已连接' }
  if (connecting) return { kind: 'pending', label: '连接中…' }
  return { kind: 'down', label: '已断开' }
}

/** 在嵌套 suites 中按 execution_case_id 优先（仅匹配 ExecutionCase.id）定位用例；
 *  无 execution_case_id 时才回退按原 case.case_id 匹配（旧协议消息）。 */
function findCase(suites: RealtimeSuite[], message: RealtimeMessage): RealtimeCase | null {
  const executionCaseId = message.execution_case_id
  if (executionCaseId != null) {
    const numeric = Number(executionCaseId)
    for (const suite of suites) {
      const caseRow = suite.cases.find((item) => item.id != null && item.id === numeric)
      if (caseRow) return caseRow
    }
    return null
  }
  const legacyCaseId = message.case_id
  if (legacyCaseId == null) return null
  const numeric = Number(legacyCaseId)
  for (const suite of suites) {
    const caseRow = suite.cases.find((item) => item.case_id === numeric)
    if (caseRow) return caseRow
  }
  return null
}

/** 在套件的前/后置步或用例步中定位 execution_step_id / (phase+step_order) 匹配的步骤。 */
function findStep(
  suites: RealtimeSuite[],
  caseRow: RealtimeCase | null,
  message: RealtimeMessage,
): RealtimeStep | null {
  const targetStepId = message.execution_step_id ?? message.step_id
  const stepOrder = Number(message.step_order)
  const phase = String(message.phase ?? '') || null

  const rawStep = (steps?: RealtimeStep[]) => {
    if (!steps) return undefined
    return steps.find((item) => {
      if (targetStepId != null && item.id != null) return item.id === Number(targetStepId)
      return item.step_order === stepOrder && (!phase || item.phase === phase)
    })
  }

  if (caseRow) {
    return rawStep(caseRow.steps) ?? null
  }

  // 可能是套件级步骤（suite_setup / suite_teardown），在 suites 中查找
  // execution_suite_id 优先（仅匹配 ExecutionSuite.id）；缺省时回退原 suite_id（旧协议）。
  const executionSuiteId = message.execution_suite_id
  if (executionSuiteId != null) {
    const numeric = Number(executionSuiteId)
    for (const suite of suites) {
      if (suite.id != null && suite.id !== numeric) continue
      return (phase === 'suite_teardown'
        ? rawStep(suite.teardown_steps)
        : rawStep(suite.setup_steps)) ?? null
    }
    return null
  }
  const legacySuiteId = message.suite_id
  if (legacySuiteId == null) return null
  const numeric = Number(legacySuiteId)
  for (const suite of suites) {
    if (suite.suite_id !== null && suite.suite_id !== numeric) continue
    return (phase === 'suite_teardown'
      ? rawStep(suite.teardown_steps)
      : rawStep(suite.setup_steps)) ?? null
  }
  return null
}

export function applyStepResult(suites: RealtimeSuite[], message: RealtimeMessage): void {
  if (!Array.isArray(suites)) return
  const caseRow = findCase(suites, message)

  const step = findStep(suites, caseRow, message)
  if (step) {
    step.status = String(message.status ?? step.status)
    step.duration = message.duration == null ? step.duration : Number(message.duration)
    step.actual_value = message.actual_value == null
      ? step.actual_value
      : String(message.actual_value)
    step.error_message = message.error_message == null
      ? step.error_message
      : String(message.error_message)
    step.artifact_id = message.artifact_id == null ? step.artifact_id : Number(message.artifact_id)
  }

  if (!caseRow) {
    const suiteRow = findSuite(suites, message)
    if (suiteRow) {
      const suiteStatus = message.case_status
      if (typeof suiteStatus === 'string' && suiteStatus) suiteRow.status = suiteStatus
      else if (message.status === 'failed') suiteRow.status = 'failed'
      else suiteRow.status = 'running'
    }
    return
  }
  const caseStatus = message.case_status
  if (typeof caseStatus === 'string' && caseStatus) {
    caseRow.status = caseStatus
  } else if (message.status === 'failed') {
    caseRow.status = 'failed'
  } else {
    caseRow.status = 'running'
  }
}

export function applyCaseStatus(suites: RealtimeSuite[], message: RealtimeMessage): void {
  const executionCase = findCase(suites, message)
  if (!executionCase) return
  executionCase.status = String(message.status ?? executionCase.status)
  executionCase.duration = message.duration == null
    ? executionCase.duration
    : Number(message.duration)
  executionCase.error_message = message.error_message == null
    ? executionCase.error_message
    : String(message.error_message)
}

export function applySuiteStatus(suites: RealtimeSuite[], message: RealtimeMessage): void {
  const executionSuite = findSuite(suites, message)
  if (!executionSuite) return
  executionSuite.status = String(message.status ?? executionSuite.status)
  executionSuite.duration = message.duration == null
    ? executionSuite.duration
    : Number(message.duration)
  executionSuite.error_message = message.error_message == null
    ? executionSuite.error_message
    : String(message.error_message)
}

export function applyAssertionResult(suites: RealtimeSuite[], message: RealtimeMessage): void {
  if (!Array.isArray(suites)) return
  const executionCase = findCase(suites, message)
  if (!executionCase) return

  const incoming = Array.isArray(message.assertions)
    ? message.assertions as RealtimeMessage[]
    : []
  executionCase.assertions ??= []
  incoming.forEach((assertion, index) => {
    const executionAssertionId = assertion.execution_assertion_id == null
      ? null
      : Number(assertion.execution_assertion_id)
    const assertionOrder = assertion.assertion_order == null
      ? null
      : Number(assertion.assertion_order)
    let targetIndex = executionAssertionId == null
      ? -1
      : executionCase.assertions!.findIndex((item) => item.id === executionAssertionId)
    if (targetIndex < 0 && assertionOrder != null) {
      targetIndex = executionCase.assertions!.findIndex(
        (item) => item.assertion_order === assertionOrder,
      )
    }
    if (targetIndex < 0 && executionAssertionId == null && assertionOrder == null) {
      targetIndex = index < executionCase.assertions!.length ? index : -1
    }
    const current = targetIndex >= 0 ? executionCase.assertions?.[targetIndex] : undefined
    const next: RealtimeAssertion = {
      ...current,
      id: current?.id ?? executionAssertionId ?? undefined,
      assertion_order: assertionOrder ?? current?.assertion_order ?? index + 1,
      assertion_type: String(assertion.type ?? current?.assertion_type ?? ''),
      expected_value: assertion.expected == null
        ? current?.expected_value ?? null
        : String(assertion.expected),
      actual_value: assertion.actual == null
        ? current?.actual_value ?? null
        : String(assertion.actual),
      status: String(assertion.status ?? current?.status ?? 'fail'),
      error_message: assertion.error_message == null
        ? current?.error_message ?? null
        : String(assertion.error_message),
      params: assertion.params == null
        ? current?.params ?? null
        : assertion.params as Record<string, unknown>,
      description: assertion.description == null
        ? current?.description ?? null
        : String(assertion.description),
    }
    if (current) Object.assign(current, next)
    else executionCase.assertions?.push(next)
  })

  if (typeof message.case_status === 'string' && message.case_status) {
    executionCase.status = message.case_status
  } else if (incoming.some((item) => ['fail', 'failed'].includes(String(item.status)))) {
    executionCase.status = 'failed'
  }
}

/** completed 时按执行终态归置所有套件/用例的待定状态。 */
export function settleExecutionSuites(suites: RealtimeSuite[], status: ExecutionStatus): void {
  for (const suite of suites) {
    if (['pending', 'running'].includes(suite.status)) {
      suite.status = status === 'passed'
        ? 'passed'
        : ['stopped', 'cancelled'].includes(status)
          ? 'stopped'
          : status === 'failed'
            ? 'failed'
            : 'error'
    }
    for (const executionCase of suite.cases) {
      if (!['pending', 'running'].includes(executionCase.status)) continue
      if (status === 'passed') executionCase.status = 'passed'
      else if (executionCase.status === 'pending') executionCase.status = 'skipped'
      else if (status === 'failed') executionCase.status = 'failed'
      else if (['stopped', 'cancelled'].includes(status)) executionCase.status = 'stopped'
      else executionCase.status = 'error'
    }
  }
}
