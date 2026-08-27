import request from '@/utils/request'

import type { PageData } from './projects'

export interface ProfileRunRef {
  app_profile_id: number
  app_release_id: number
  expected_profile_revision: number
  expected_test_asset_revision: number
}

export type ExecutionStatus =
  | 'queued'
  | 'running'
  | 'stopping'
  | 'passed'
  | 'failed'
  | 'error'
  | 'stopped'
  | 'cancelled'

export type ExecutionStepPhase =
  | 'setup'
  | 'main'
  | 'teardown'
  | 'case_setup'
  | 'case_main'
  | 'case_teardown'
  | 'suite_setup'
  | 'suite_teardown'

export interface ExecutionStep {
  id: number
  step_order: number
  action: string
  phase?: ExecutionStepPhase
  parameters: Record<string, unknown>
  status: string
  duration: number | null
  actual_value: string | null
  error_message: string | null
  artifact_id?: number | null
}

export interface ExecutionAssertion {
  id: number
  assertion_order?: number | null
  assertion_type: string
  expected_value: string | null
  actual_value: string | null
  status: string
  error_message: string | null
  params?: Record<string, unknown> | null
  description?: string | null
}

export interface ExecutionCase {
  id: number
  case_id: number
  case_name: string
  module_name: string | null
  status: string
  started_at: string | null
  finished_at: string | null
  duration: number | null
  error_message: string | null
  steps?: ExecutionStep[]
  assertions?: ExecutionAssertion[]
}

export interface ExecutionSuite {
  id: number
  suite_id: number | null
  suite_name: string
  suite_order: number
  status: string
  duration: number | null
  error_message: string | null
  setup_steps: ExecutionStep[]
  cases: ExecutionCase[]
  teardown_steps: ExecutionStep[]
}

// 执行摘要：可执行的套件/用例计数（后端 _execution_summary）
export interface ExecutionSummary {
  executable_suites?: number
  executable_cases?: number
  suite_total?: number
  case_total?: number
  [key: string]: number | undefined
}

export interface Execution {
  id: number
  project_id: number
  type: string
  suite_id: number | null
  case_id: number | null
  device_id: number | null
  status: ExecutionStatus
  parameters: Record<string, unknown>
  timeout_seconds: number
  started_at: string | null
  finished_at: string | null
  duration: number | null
  created_by: number | null
  retry_of: number | null
  created_at: string
  app_profile_id: number | null
  app_profile_name_snapshot: string | null
  app_release_id: number | null
  app_release_version_snapshot: string | null
  profile_revision: number | null
  test_asset_revision: number | null
}

export interface ExecutionDetail extends Execution {
  project_name: string | null
  device_name: string | null
  created_by_name: string | null
  // 方案 §4.3：执行详情嵌套 suites（套件级执行单位）
  suites?: ExecutionSuite[]
  summary?: ExecutionSummary
  // 兼容：单用例/历史执行仍可能返回扁平 cases
  cases?: ExecutionCase[]
}

export interface ExecutionLog {
  id: number
  execution_id: number
  level: string
  message: string
  source: string
  created_at: string
}

export interface ExecutionListItem {
  id: number
  project_id: number
  type: string
  suite_id: number | null
  case_id: number | null
  device_id: number | null
  status: ExecutionStatus
  timeout_seconds: number
  started_at: string | null
  finished_at: string | null
  duration: number | null
  retry_of: number | null
  created_at: string
  case_name: string | null
  suite_name: string | null
  project_name?: string | null
  device_name?: string | null
  created_by_name?: string | null
}

export interface RunOptions {
  device_id?: number | null
  parameters?: Record<string, unknown>
  timeout_seconds?: number
  // 方案 §4.8：执行创建必填档案/版本与 revision（required 语义）
  app_profile_id?: number
  app_release_id?: number
  expected_profile_revision?: number
  expected_test_asset_revision?: number
}

export interface ExecutionRunSettings {
  use_pre_steps: boolean
  use_post_steps: boolean
  attach_to_current_app: boolean
}

export function listExecutions(params?: {
  project_id?: number
  status?: string
  type?: string
  keyword?: string
  device_id?: number
  created_from?: string
  created_to?: string
  page?: number
  page_size?: number
}) {
  return request.get<PageData<ExecutionListItem>>('/executions', { params })
}

export function getExecution(id: number) {
  return request.get<ExecutionDetail>(`/executions/${id}`)
}

export function getExecutionLogs(id: number, params?: { after_timestamp?: string; page?: number; page_size?: number }) {
  return request.get<PageData<ExecutionLog>>(`/executions/${id}/logs`, { params })
}

export function createCaseExecution(caseId: number, data: RunOptions) {
  return request.post<Execution>(`/executions/cases/${caseId}`, data)
}

export function createSuiteExecution(suiteId: number, data: RunOptions) {
  return request.post<Execution>(`/executions/suites/${suiteId}`, data)
}

export function createBatchExecution(data: RunOptions & { suite_ids: number[] }) {
  return request.post<Execution>('/executions/suites/batch', data)
}

export function stopExecution(id: number) {
  return request.post<{ execution_id: number; status: string }>(`/executions/${id}/stop`)
}

export interface RetryOptions {
  device_id: number
  timeout_seconds?: number
}

export function retryExecution(id: number, data: RetryOptions) {
  return request.post<Execution>(`/executions/${id}/retry`, data)
}

export const EXECUTION_STATUS: { value: string; label: string; type: 'success' | 'info' | 'warning' | 'danger' | 'primary' }[] = [
  { value: 'queued', label: '排队中', type: 'info' },
  { value: 'running', label: '运行中', type: 'primary' },
  { value: 'stopping', label: '停止中', type: 'warning' },
  { value: 'passed', label: '通过', type: 'success' },
  { value: 'failed', label: '失败', type: 'danger' },
  { value: 'error', label: '异常', type: 'danger' },
  { value: 'stopped', label: '已停止', type: 'info' },
  { value: 'cancelled', label: '已取消', type: 'info' },
]

export function executionStatusMeta(status: string) {
  return EXECUTION_STATUS.find((x) => x.value === status) ?? { value: status, label: status, type: 'info' as const }
}
