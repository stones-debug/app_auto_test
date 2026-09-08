/**
 * Small, DOM-free helpers used by the execution detail auto-follow behaviour.
 * The websocket uses execution-instance ids (rather than the source case/suite
 * ids), so these helpers deliberately keep those ids distinct.
 */
export interface ActiveExecutionTarget {
  execution_node_id: number
  execution_case_id: number | null
  execution_suite_id: number | null
}

function numericId(value: unknown): number | null {
  if (value == null || value === '') return null
  const id = Number(value)
  return Number.isInteger(id) && id > 0 ? id : null
}

/** Extract a node_started target without accepting an invalid node id. */
export function extractActiveExecutionTarget(
  message: Record<string, unknown>,
): ActiveExecutionTarget | null {
  const nodeId = numericId(message.execution_node_id)
  if (nodeId == null) return null
  return {
    execution_node_id: nodeId,
    execution_case_id: numericId(message.execution_case_id),
    execution_suite_id: numericId(message.execution_suite_id),
  }
}

interface RunningItem {
  id?: unknown
  status?: unknown
  assertions?: RunningItem[]
}

interface RunningCase {
  id?: unknown
  nodes?: RunningItem[]
  steps?: RunningItem[]
  assertions?: RunningItem[]
}

interface RunningSuite {
  id?: unknown
  nodes?: RunningItem[]
  setup_steps?: RunningItem[]
  cases?: RunningCase[]
  teardown_steps?: RunningItem[]
}

function isRunning(item: RunningItem | undefined): boolean {
  return String(item?.status ?? '').toLowerCase() === 'running'
}

function runningItemId(items: RunningItem[] | undefined): number | null {
  return items?.find(isRunning)?.id == null ? null : numericId(items.find(isRunning)?.id)
}

function targetForNode(
  nodeId: number | null,
  executionSuiteId: number | null,
  executionCaseId: number | null,
): ActiveExecutionTarget | null {
  if (nodeId == null) return null
  return {
    execution_node_id: nodeId,
    execution_case_id: executionCaseId,
    execution_suite_id: executionSuiteId,
  }
}

/**
 * Recover the currently running execution node from a REST/Resync snapshot.
 * Only execution-instance `id` fields are read; source asset IDs are ignored.
 */
export function findRunningExecutionTarget(
  suites: ReadonlyArray<RunningSuite> | null | undefined,
): ActiveExecutionTarget | null {
  if (!suites) return null
  for (const suite of suites) {
    const executionSuiteId = numericId(suite.id)
    const suiteNodeId = runningItemId(suite.nodes)
      ?? runningItemId(suite.setup_steps)
      ?? runningItemId(suite.teardown_steps)
    const suiteTarget = targetForNode(suiteNodeId, executionSuiteId, null)
    if (suiteTarget && executionSuiteId != null) return suiteTarget

    for (const executionCase of suite.cases ?? []) {
      const executionCaseId = numericId(executionCase.id)
      if (executionSuiteId == null || executionCaseId == null) continue
      const nodeId = runningItemId(executionCase.nodes)
      if (nodeId != null) return targetForNode(nodeId, executionSuiteId, executionCaseId)

      // Legacy snapshots store actions in steps and assertions as children of
      // the action. Keep the same execution-step/assertion ID semantics.
      const runningStep = executionCase.steps?.find(isRunning)
      const stepId = numericId(runningStep?.id)
      if (stepId != null) return targetForNode(stepId, executionSuiteId, executionCaseId)
      const runningAssertion = executionCase.steps
        ?.flatMap((step) => step.assertions ?? [])
        .find(isRunning)
        ?? executionCase.assertions?.find(isRunning)
      const assertionId = numericId(runningAssertion?.id)
      if (assertionId != null) return targetForNode(assertionId, executionSuiteId, executionCaseId)
    }
  }
  return null
}

export interface ExecutionPath {
  execution_node_id: number | null
  execution_case_id: number | null
  execution_suite_id: number | null
}

/** Match a rendered row to the exact execution-instance path from the WS. */
export function matchesExecutionPath(
  target: ActiveExecutionTarget,
  path: ExecutionPath,
): boolean {
  return target.execution_node_id === path.execution_node_id
    && (target.execution_case_id == null || target.execution_case_id === path.execution_case_id)
    && (target.execution_suite_id == null || target.execution_suite_id === path.execution_suite_id)
}

/** Stable identity for a case disclosure entry, independent of array indexes. */
export function executionCaseKey(executionSuiteId: number | null | undefined, executionCaseId: number | null | undefined): string {
  return `${executionSuiteId ?? 'virtual'}:${executionCaseId ?? 'unknown'}`
}

/**
 * Collapse only on the first non-passed -> passed transition. A later refresh
 * with the same status, including after the user reopens the case, is inert.
 */
export function isFirstPassedTransition(
  previousStatus: string | undefined,
  nextStatus: string,
  alreadyHandled: boolean,
): boolean {
  return !alreadyHandled && previousStatus != null && previousStatus !== 'passed' && nextStatus === 'passed'
}
