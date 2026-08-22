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
}

export interface ReportStep {
  id: number
  step_order: number
  action: string
  status: string
  duration: number | null
  actual_value: string | null
  error_message: string | null
  screenshot: string | null
}

export interface ReportAssertion {
  id: number
  assertion_type: string
  expected_value: string | null
  actual_value: string | null
  status: string
  error_message: string | null
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
}

export interface ReportDetail {
  execution: ReportExecution
  report: ReportSummary
  cases: ReportCase[]
  logs: ReportLog[]
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
