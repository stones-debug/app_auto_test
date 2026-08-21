import request from '@/utils/request'

export interface DashboardStats {
  project_count: number
  period_execution_count: number
  active_execution_count: number
  success_rate: number
  available_device_count: number
  device_count: number
}

export interface TrendPoint {
  date: string
  total: number
  passed: number
  failed: number
  error: number
}

export interface RecentExecution {
  id: number
  project_id: number
  type: string
  status: string
  case_id: number | null
  suite_id: number | null
  created_at: string | null
}

export interface DashboardOverview {
  range: string
  generated_at: string
  stats: DashboardStats
  status_counts: Record<string, number>
  trend: TrendPoint[]
  recent_executions: RecentExecution[]
}

export function getDashboardOverview(params?: { range?: string; project_id?: number }) {
  return request.get<DashboardOverview>('/dashboard/overview', { params })
}