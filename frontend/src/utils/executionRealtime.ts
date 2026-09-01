import type { ExecutionStatus } from '@/api/executions'

type RealtimeMessage = Record<string, unknown>

interface RealtimeStep {
  id?: number
  action?: string
  parameters?: Record<string, unknown>
  step_order: number
  status: string
  duration?: number | null
  actual_value?: string | null
  error_message?: string | null
  artifact_id?: string | null
  phase?: string | null
  assertions?: RealtimeAssertion[]
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

interface RealtimeNode {
  id?: number
  kind: 'action' | 'assertion'
  node_order: number
  status: string
  duration?: number | null
  actual_value?: string | null
  expected_value?: string | null
  error_message?: string | null
  attempt_count?: number
  artifact_id?: string | null
}

interface RealtimeCase {
  id?: number
  case_id: number
  status: string
  duration?: number | null
  error_message?: string | null
  steps?: RealtimeStep[]
  nodes?: RealtimeNode[]
  /** 兼容旧消息消费者；新的 UI 从步骤 assertions 渲染。 */
  assertions?: RealtimeAssertion[]
}

const TERMINAL_STATUS_PRIORITY: Record<string, number> = {
  passed: 1,
  skipped: 2,
  stopped: 3,
  failed: 4,
  error: 5,
}

function isTerminalStatus(status: string): boolean {
  return Object.prototype.hasOwnProperty.call(TERMINAL_STATUS_PRIORITY, status)
}

/** 按 error > failed > stopped > skipped > passed 聚合节点状态。 */
export function aggregateRealtimeStatuses(statuses: Iterable<string>): string {
  const values = [...statuses].map((status) => String(status).toLowerCase())
  if (!values.length) return 'skipped'
  for (const status of ['error', 'failed', 'stopped']) {
    if (values.includes(status)) return status
  }
  if (values.includes('running')) return 'running'
  if (values.includes('pending')) return 'pending'
  if (values.includes('skipped')) return 'skipped'
  if (values.length && values.every((status) => status === 'passed')) return 'passed'
  return 'error'
}

/** 合并单条状态消息，禁止低优先级状态覆盖已经存在的终态。 */
export function mergeRealtimeStatus(current: string, incoming: string): string {
  const previous = String(current || 'pending').toLowerCase()
  const next = String(incoming || previous).toLowerCase()
  if (!isTerminalStatus(previous)) {
    if (isTerminalStatus(next)) return next
    if (next === 'running') return 'running'
    return previous === 'running' ? previous : next
  }
  if (!isTerminalStatus(next)) return previous
  return (TERMINAL_STATUS_PRIORITY[next] ?? 0) >= (TERMINAL_STATUS_PRIORITY[previous] ?? 0)
    ? next
    : previous
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

function findSuiteStepByNodeId(
  suites: RealtimeSuite[],
  message: RealtimeMessage,
): { suite: RealtimeSuite; step: RealtimeStep } | null {
  const suite = findSuite(suites, message)
  if (!suite) return null
  const nodeId = message.execution_node_id == null ? null : Number(message.execution_node_id)
  if (nodeId == null) return null
  const step = [...(suite.setup_steps ?? []), ...(suite.teardown_steps ?? [])]
    .find((item) => item.id != null && item.id === nodeId)
  return step ? { suite, step } : null
}

function updateStepFromNodeMessage(step: RealtimeStep, message: RealtimeMessage): void {
  step.status = String(message.status ?? step.status)
  if (message.duration != null) step.duration = Number(message.duration)
  if (message.actual_value != null) step.actual_value = String(message.actual_value)
  if (message.error_message != null) step.error_message = String(message.error_message)
  if (message.artifact_id != null) step.artifact_id = String(message.artifact_id)
}

function aggregateCaseNodeStatus(executionCase: RealtimeCase, fallback: string): string {
  if (!executionCase.nodes?.length) return fallback
  const nodeStatus = aggregateRealtimeStatuses(executionCase.nodes.map((node) => node.status))
  // case_status 可能先于节点结果到达；已有 running 也不能被剩余 pending
  // 节点降级。终态则按统一优先级与节点聚合结果合并。
  return mergeRealtimeStatus(fallback, nodeStatus)
}

function aggregateSuiteStatus(suite: RealtimeSuite): string {
  const childStatus = aggregateRealtimeStatuses([
    ...(suite.setup_steps ?? []).map((step) => step.status),
    ...suite.cases.map((executionCase) => executionCase.status),
    ...(suite.teardown_steps ?? []).map((step) => step.status),
  ])
  return mergeRealtimeStatus(suite.status, childStatus)
}

export function applyStepResult(suites: RealtimeSuite[], message: RealtimeMessage): void {
  if (!Array.isArray(suites)) return
  const caseRow = findCase(suites, message)

  const step = findStep(suites, caseRow, message)
  if (step) {
    updateStepFromNodeMessage(step, message)
  }

  if (!caseRow) {
    const suiteRow = findSuite(suites, message)
    if (suiteRow) {
      const suiteStatus = message.case_status
      if (typeof suiteStatus === 'string' && suiteStatus) {
        suiteRow.status = mergeRealtimeStatus(suiteRow.status, suiteStatus)
      } else {
        suiteRow.status = mergeRealtimeStatus(
          suiteRow.status,
          message.status === 'failed' ? 'failed' : 'running',
        )
      }
    }
    return
  }
  const caseStatus = message.case_status
  if (typeof caseStatus === 'string' && caseStatus) {
    caseRow.status = mergeRealtimeStatus(caseRow.status, caseStatus)
  } else if (message.status === 'failed') {
    caseRow.status = mergeRealtimeStatus(caseRow.status, 'failed')
  } else {
    caseRow.status = mergeRealtimeStatus(caseRow.status, 'running')
  }
}

/** V3：节点开始执行时立即显示 running，并按全部节点重新计算用例状态。 */
export function applyNodeStarted(suites: RealtimeSuite[], message: RealtimeMessage): void {
  if (!Array.isArray(suites)) return
  const executionCase = findCase(suites, message)
  if (executionCase?.nodes) {
    const nodeId = message.execution_node_id == null ? null : Number(message.execution_node_id)
    const node = executionCase.nodes.find((item) => nodeId != null && item.id === nodeId)
    if (!node) return
    node.status = 'running'
    executionCase.status = aggregateCaseNodeStatus(executionCase, 'running')
    return
  }
  const suiteNode = findSuiteStepByNodeId(suites, message)
  if (!suiteNode) return
  suiteNode.step.status = 'running'
  suiteNode.suite.status = aggregateSuiteStatus(suiteNode.suite)
}

/** V3：按 execution_node_id 合并统一节点结果，不依赖节点数组位置。 */
export function applyNodeResult(suites: RealtimeSuite[], message: RealtimeMessage): void {
  const executionCase = findCase(suites, message)
  if (!executionCase?.nodes) {
    const suiteNode = findSuiteStepByNodeId(suites, message)
    if (!suiteNode) return
    updateStepFromNodeMessage(suiteNode.step, message)
    suiteNode.suite.status = aggregateSuiteStatus(suiteNode.suite)
    return
  }
  const nodeId = message.execution_node_id == null ? null : Number(message.execution_node_id)
  const node = executionCase.nodes.find((item) => nodeId != null && item.id === nodeId)
  if (!node) return
  node.status = mergeRealtimeStatus(node.status, String(message.status ?? node.status))
  if (message.duration != null) node.duration = Number(message.duration)
  if (message.actual_value != null) node.actual_value = String(message.actual_value)
  if (message.expected_value != null) node.expected_value = String(message.expected_value)
  if (message.error_message != null) node.error_message = String(message.error_message)
  if (message.attempt_count != null) node.attempt_count = Number(message.attempt_count)
  if (message.artifact_id != null) node.artifact_id = String(message.artifact_id)
  executionCase.status = aggregateCaseNodeStatus(executionCase, executionCase.status)
}

export function applyCaseStatus(suites: RealtimeSuite[], message: RealtimeMessage): void {
  const executionCase = findCase(suites, message)
  if (!executionCase) return
  executionCase.status = mergeRealtimeStatus(
    executionCase.status,
    String(message.status ?? executionCase.status),
  )
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
  executionSuite.status = mergeRealtimeStatus(
    executionSuite.status,
    String(message.status ?? executionSuite.status),
  )
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
  let executionStep = findStep(suites, executionCase, message) ?? executionCase.steps?.[0]
  if (!executionStep) {
    executionCase.steps ??= []
    executionStep = {
      id: message.execution_step_id == null ? undefined : Number(message.execution_step_id),
      step_order: Number(message.step_order ?? 0),
      action: '',
      phase: 'case_main',
      parameters: {},
      status: 'passed',
      assertions: executionCase.assertions ?? [],
    }
    executionCase.steps.push(executionStep as RealtimeStep)
  }
  if (!executionStep) return
  const resolvedStep = executionStep as RealtimeStep

  const incoming = Array.isArray(message.assertions)
    ? message.assertions as RealtimeMessage[]
    : []
  resolvedStep.assertions ??= []
  executionCase.assertions = resolvedStep.assertions
  incoming.forEach((assertion, index) => {
    const executionAssertionId = assertion.execution_assertion_id == null
      ? null
      : Number(assertion.execution_assertion_id)
    const assertionOrder = assertion.assertion_order == null
      ? null
      : Number(assertion.assertion_order)
    let targetIndex = executionAssertionId == null
      ? -1
      : resolvedStep.assertions!.findIndex((item) => item.id === executionAssertionId)
    if (targetIndex < 0 && assertionOrder != null) {
      targetIndex = resolvedStep.assertions!.findIndex(
        (item) => item.assertion_order === assertionOrder,
      )
    }
    if (targetIndex < 0 && executionAssertionId == null && assertionOrder == null) {
      targetIndex = index < resolvedStep.assertions!.length ? index : -1
    }
    const current = targetIndex >= 0 ? resolvedStep.assertions?.[targetIndex] : undefined
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
    else resolvedStep.assertions?.push(next)
  })

  if (typeof message.case_status === 'string' && message.case_status) {
    executionCase.status = mergeRealtimeStatus(executionCase.status, message.case_status)
  } else if (incoming.some((item) => ['fail', 'failed'].includes(String(item.status)))) {
    executionCase.status = mergeRealtimeStatus(executionCase.status, 'failed')
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
