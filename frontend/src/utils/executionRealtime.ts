import type { ExecutionStatus } from '@/api/executions'

type RealtimeMessage = Record<string, unknown>

interface RealtimeStep {
  step_order: number
  status: string
  duration?: number | null
  actual_value?: string | null
  error_message?: string | null
  artifact_id?: number | null
}

interface RealtimeCase {
  case_id: number
  status: string
  steps?: RealtimeStep[]
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

export function applyStepResult(cases: RealtimeCase[], message: RealtimeMessage): void {
  const executionCase = cases.find((item) => item.case_id === Number(message.case_id))
  if (!executionCase) return

  const step = executionCase.steps?.find(
    (item) => item.step_order === Number(message.step_order),
  )
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

  const caseStatus = message.case_status
  if (typeof caseStatus === 'string' && caseStatus) {
    executionCase.status = caseStatus
  } else if (message.status === 'failed') {
    executionCase.status = 'failed'
  } else {
    executionCase.status = 'running'
  }
}

export function applyAssertionResult(cases: RealtimeCase[], message: RealtimeMessage): void {
  const executionCase = cases.find((item) => item.case_id === Number(message.case_id))
  if (!executionCase) return
  if (typeof message.case_status === 'string' && message.case_status) {
    executionCase.status = message.case_status
  }
}

export function settleExecutionCases(cases: RealtimeCase[], status: ExecutionStatus): void {
  for (const executionCase of cases) {
    if (!['pending', 'running'].includes(executionCase.status)) continue
    if (status === 'passed') executionCase.status = 'passed'
    else if (executionCase.status === 'pending') executionCase.status = 'skipped'
    else if (status === 'failed') executionCase.status = 'failed'
    else if (['stopped', 'cancelled'].includes(status)) executionCase.status = 'stopped'
    else executionCase.status = 'error'
  }
}
