import request from '@/utils/request'

import type { VariableChip } from '@/utils/caseVariables'
import type { Step } from './cases'

export interface Suite {
  id: number
  project_id: number
  name: string
  description?: string | null
  status: string
  /** 套件模块树（scope='suite'）的模块；null 表示未分组 */
  module_id?: number | null
  module_name?: string | null
  created_by?: number | null
  case_count: number
  // 方案 §2：套件前后置步骤（Action Step，与用例步骤同构）
  setup_steps?: Step[]
  teardown_steps?: Step[]
  created_at: string
  updated_at: string
}

/** 行内变量摘要（最多 2 项），与共享展示口径一致。 */
export type SuiteVariablePreview = VariableChip

export interface SuiteCase {
  id: number
  case_id: number
  case_name: string
  module_name?: string | null
  sort_order: number
  variable_count: number
  variables_preview: SuiteVariablePreview[]
}

export interface SuiteCaseVariable {
  name: string
  reference_count: number
  status: string
  display_value: string
  inherited_value: string | null
  inherited_scope: string | null
  override_enabled: boolean
  override_value: string
}

export interface SuiteCaseVariables {
  suite_id: number
  membership_id: number
  case_id: number
  case_name: string
  test_asset_revision: number
  total: number
  variables: SuiteCaseVariable[]
}

export interface Variable {
  id: number
  scope: string
  project_id?: number | null
  suite_id?: number | null
  case_id?: number | null
  name: string
  /** 敏感变量接口返回掩码；编辑时不得将该值回写。 */
  value: string
  kind: 'fixed' | 'random_integer' | 'random_choice'
  spec?: { min?: number; max?: number; items?: string[] } | null
  description?: string | null
  is_sensitive: boolean
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

export interface SuiteListParams {
  page?: number
  page_size?: number
  keyword?: string
  status?: string
  /** 具体模块：后端会包含其子孙模块 */
  module_id?: number
  /** 未分组（`module_id IS NULL`）。**不要**用 `module_id: null` 表达，axios 会丢弃 null 参数 */
  ungrouped?: boolean
}

export async function listSuites(projectId: number, params: SuiteListParams = {}) {
  return request.get<SuitePage>(`/projects/${projectId}/suites`, { params })
}

export function createSuite(projectId: number, data: {
  name: string
  description?: string
  module_id?: number | null
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

export function getSuiteCaseVariables(suiteId: number, membershipId: number) {
  return request.get<SuiteCaseVariables>(`/suites/${suiteId}/cases/${membershipId}/variables`)
}

/** 增量覆盖：字符串=设置编排项覆盖，null=删除覆盖并恢复继承。 */
export function patchSuiteCaseVariables(
  suiteId: number,
  membershipId: number,
  updates: Record<string, string | null>,
) {
  return request.patch<SuiteCaseVariables>(
    `/suites/${suiteId}/cases/${membershipId}/variable-overrides`,
    { updates },
  )
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

export function updateVariable(id: number, data: { value?: string; kind?: Variable['kind']; spec?: Variable['spec']; description?: string | null; is_sensitive?: boolean }) {
  return request.put<Variable>(`/variables/${id}`, data)
}

export function deleteVariable(id: number) {
  return request.delete<void>(`/variables/${id}`)
}
