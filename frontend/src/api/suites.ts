import request from '@/utils/request'

import type { Step } from './cases'

export interface Suite {
  id: number
  project_id: number
  name: string
  description?: string | null
  status: string
  created_by?: number | null
  case_count: number
  // 方案 §2：套件前后置步骤（Action Step，与用例步骤同构）
  setup_steps?: Step[]
  teardown_steps?: Step[]
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

export interface SuitePage {
  total: number
  page: number
  page_size: number
  items: Suite[]
}

export const VARIABLE_SCOPES = [
  { value: 'global', label: '全局' },
  { value: 'project', label: '项目' },
  { value: 'suite', label: '套件' },
  { value: 'case', label: '用例' },
]

export async function listSuites(projectId: number) {
  const data = await request.get<SuitePage>(`/projects/${projectId}/suites`)
  return data.items
}

export function createSuite(projectId: number, data: {
  name: string
  description?: string
  setup_steps?: Step[]
  teardown_steps?: Step[]
}) {
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

export function addSuiteCases(suiteId: number, caseIds: number[]) {
  return request.post<SuiteCase[]>(`/suites/${suiteId}/cases`, { case_ids: caseIds })
}

export function reorderSuiteCases(suiteId: number, membershipIds: number[]) {
  return request.put<void>(`/suites/${suiteId}/cases/order`, { membership_ids: membershipIds })
}

export function removeSuiteCase(suiteId: number, membershipId: number) {
  return request.delete<void>(`/suites/${suiteId}/cases/${membershipId}`)
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
