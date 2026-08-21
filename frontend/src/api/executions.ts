import request from '@/utils/request'

import type { PageData } from './projects'

export type ExecutionStatus =
  | 'queued'
  | 'running'
  | 'stopping'
  | 'passed'
  | 'failed'
  | 'error'
  | 'stopped'
  | 'cancelled'

export interface ExecutionStep {
  id: number
  step_order: number
  action: string
  parameters: Record<string, unknown>
  status: string
  duration: number | null
  actual_value: string | null
  error_message: string | null
}

export interface ExecutionAssertion {
  id: number
  assertion_type: string
  expected_value: string | null
  actual_value: string | null
  status: string
  error_message: string | null
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
}

export interface ExecutionDetail extends Execution {
  project_name: string | null
  device_name: string | null
  created_by_name: string | null
  cases: ExecutionCase[]
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

export function retryExecution(id: number) {
  return request.post<Execution>(`/executions/${id}/retry`)
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
