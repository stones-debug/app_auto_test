import request from '@/utils/request'

import type { PageData } from './projects'

export interface ReportListItem {
  id: number
  execution_id: number
  total: number
  passed: number
  failed: number
  error_count: number
  skipped: number
  success_rate: number
  duration: number | null
  has_report: boolean
  created_at: string
  project_id: number | null
  project_name?: string | null
  device_name?: string | null
  execution_status: string | null
  execution_type: string | null
  case_name: string | null
  suite_name: string | null
  finished_at?: string | null
  // 方案 §7：档案/版本 + N/A 数量
  app_profile_name?: string | null
  app_release_version?: string | null
  not_applicable?: number
}

export interface ReportExecution {
  id: number
  project_id: number
  type: string
  suite_id: number | null
  case_id: number | null
  device_id: number | null
  status: string
  parameters: Record<string, unknown>
  timeout_seconds: number
  started_at: string | null
  finished_at: string | null
  duration: number | null
  retry_of: number | null
  created_at: string
  app_profile_id: number | null
  app_profile_name: string | null
  app_release_id: number | null
  app_release_version: string | null
  profile_revision: number | null
  test_asset_revision: number | null
  profile_resolution_summary: Record<string, number>
}

export type ReportStepPhase =
  | 'setup'
  | 'main'
  | 'teardown'
  | 'case_setup'
  | 'case_main'
  | 'case_teardown'
  | 'suite_setup'
  | 'suite_teardown'

export interface ReportStep {
  id: number
  step_order: number
  action: string
  phase?: ReportStepPhase
  parameters: Record<string, unknown>
  status: string
  duration: number | null
  actual_value: string | null
  error_message: string | null
  screenshot: string | null
}

export interface ReportAssertion {
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

export interface ReportCase {
  id: number
  case_id: number
  case_name: string
  module_name: string | null
  status: string
  duration: number | null
  error_message: string | null
  elements: Record<string, unknown>
  steps: ReportStep[]
  assertions: ReportAssertion[]
}

// 方案 §4.4：嵌套套件（套件前后置 + 套件内用例）
export interface ReportSuite {
  id: number
  suite_id: number | null
  suite_name: string
  suite_order: number
  status: string
  duration: number | null
  error_message: string | null
  setup_steps: ReportStep[]
  cases: ReportCase[]
  teardown_steps: ReportStep[]
}

export interface ReportLog {
  id: number
  level: string
  message: string
  source: string
  created_at: string | null
}

export interface ReportSummary {
  id: number | null
  total: number
  passed: number
  failed: number
  error_count: number
  skipped: number
  success_rate: number
  // 方案 §7：不适用（N/A）计数与排除摘要
  not_applicable?: number
  exclusion_summary?: Record<string, number>
  // 方案 §4.4：套件/步骤三层统计
  suite_total?: number
  suite_passed?: number
  suite_failed?: number
  suite_error_count?: number
  suite_skipped?: number
  suite_success_rate?: number
  step_total?: number
  step_passed?: number
  step_failed?: number
  step_error_count?: number
  step_skipped?: number
  step_success_rate?: number
  not_applicable_suites?: number
}

export interface ReportExclusion {
  target_type: string
  path: string
  reason_code: string
  reason_note: string | null
  source_type: string | null
  node_key?: string | null
  // 方案 §7.2：层级树所需结构字段（历史执行可能缺失）
  suite_id?: number | null
  case_id?: number | null
  phase?: string | null
}

export interface ReportDetail {
  execution: ReportExecution
  report: ReportSummary
  cases: ReportCase[]
  // 方案 §4.4：嵌套套件（套件级执行单位）
  suites: ReportSuite[]
  logs: ReportLog[]
  // 方案 §7.2：不适用内容清单
  exclusions?: ReportExclusion[]
  // Step 8：日志截断元数据
  logs_total?: number
  logs_truncated?: boolean
}

export function listReports(params?: {
  project_id?: number
  execution_id?: number
  status?: string
  type?: string
  keyword?: string
  created_from?: string
  created_to?: string
  app_profile_id?: number
  app_release_id?: number
  page?: number
  page_size?: number
}) {
  return request.get<PageData<ReportListItem>>('/reports', { params })
}

export function getReport(id: number) {
  return request.get<ReportDetail>(`/reports/${id}`)
}

export function getReportDetail(id: number) {
  return request.get<ReportDetail>(`/reports/${id}/detail`)
}

export async function downloadReport(id: number) {
  const blob = await request.get<Blob>(`/reports/${id}/download`, { responseType: 'blob' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `execution_${id}_report.html`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function reportFileUrl(reportId: number, filename: string): string {
  const encoded = filename
    .split('/')
    .map(encodeURIComponent)
    .join('/')
  return `/api/reports/${reportId}/files/${encoded}`
}

export async function findReportByExecution(executionId: number): Promise<number | null> {
  const data = await listReports({ execution_id: executionId, page_size: 1 })
  return data.items.length ? data.items[0].id : null
}
