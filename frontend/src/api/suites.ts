import request from '@/utils/request'

export interface Suite {
  id: number
  project_id: number
  name: string
  description?: string | null
  status: string
  created_by?: number | null
  case_count: number
  created_at: string
  updated_at: string
}

export interface SuiteCase {
  id: number
  case_id: number
  case_name: string
  module_name?: string | null
  sort_order: number
}

export interface Variable {
  id: number
  scope: string
  project_id?: number | null
  suite_id?: number | null
  case_id?: number | null
  name: string
  value: string
  description?: string | null
  created_at: string
  updated_at: string
}

export const VARIABLE_SCOPES = [
  { value: 'global', label: '全局' },
  { value: 'project', label: '项目' },
  { value: 'suite', label: '套件' },
  { value: 'case', label: '用例' },
]

export function listSuites(projectId: number) {
  return request.get<Suite[]>(`/projects/${projectId}/suites`)
}

export function createSuite(projectId: number, data: { name: string; description?: string }) {
  return request.post<Suite>(`/projects/${projectId}/suites`, data)
}

export function getSuite(id: number) {
  return request.get<Suite>(`/suites/${id}`)
}

export function updateSuite(id: number, data: Partial<Suite>) {
  return request.put<Suite>(`/suites/${id}`, data)
}

export function deleteSuite(id: number) {
  return request.delete<void>(`/suites/${id}`)
}

export function listSuiteCases(suiteId: number) {
  return request.get<SuiteCase[]>(`/suites/${suiteId}/cases`)
}

export function addSuiteCase(suiteId: number, caseId: number) {
  return request.post<SuiteCase>(`/suites/${suiteId}/cases`, { case_id: caseId })
}

export function reorderSuiteCases(suiteId: number, order: number[]) {
  return request.put<void>(`/suites/${suiteId}/cases/order`, { order })
}

export function removeSuiteCase(suiteId: number, caseId: number) {
  return request.delete<void>(`/suites/${suiteId}/cases/${caseId}`)
}

export function listVariables(params?: {
  scope?: string
  project_id?: number
  suite_id?: number
  case_id?: number
}) {
  return request.get<Variable[]>('/variables', { params })
}

export function createVariable(data: Partial<Variable>) {
  return request.post<Variable>('/variables', data)
}

export function updateVariable(id: number, data: { value?: string; description?: string }) {
  return request.put<Variable>(`/variables/${id}`, data)
}

export function deleteVariable(id: number) {
  return request.delete<void>(`/variables/${id}`)
}