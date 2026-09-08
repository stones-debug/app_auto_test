import request from '@/utils/request'
export { apiErrorDetail } from '@/utils/request'
export type { ApiErrorDetail } from '@/utils/request'

import type { SmartLocatorConfig } from '@/utils/smartLocator'
import type { PageData } from './projects'

export interface AppProfileSummary {
  id: number
  project_id: number
  name: string
  code: string
  description: string | null
  status: string
  inherit_all: boolean
  revision: number
  release_count: number
  skip_counts: Record<'suite' | 'case' | 'step' | 'assertion', number>
  override_counts: Record<'element' | 'variable' | 'node', number>
  created_by: number | null
  updated_by: number | null
  created_at: string
  updated_at: string
}

export interface AppProfileRelease {
  id: number
  profile_id: number
  version: string
  build_number: string | null
  description: string | null
  status: string
  created_by: number | null
  created_at: string
  updated_at: string
}

export interface ProfileNode {
  node_type: 'suite' | 'case' | 'step' | 'assertion' | 'suite_step'
  id: number | null
  node_key: string | null
  element_id?: number | null
  element_name?: string | null
  name: string
  registry_key?: string | null
  phase?: string | null
  order?: number | null
  effective_status: string
  status_source: string
  reason: { code: string; note: string } | null
  override_count: number
  /** 公共节点中允许覆盖的当前参数；编辑器以此预填并仅提交差异。 */
  override_template?: Record<string, unknown>
  child_count?: number
  difference_count?: number
  has_children: boolean
  // 套件节点：前后置步骤计数（工作台树状展开用）
  setup_step_count?: number
  teardown_step_count?: number
  updated_at?: string | null
}

export interface WorkspacePage {
  profile_revision: number
  test_asset_revision: number
  total: number
  page: number
  page_size: number
  items: ProfileNode[]
}

export interface DifferenceRow {
  target_type: string
  path: string
  reason_code: string | null
  reason_note: string | null
  source_type: string | null
  override: boolean
}

export interface PreviewCounts {
  source_suites: number
  source_cases: number
  executable_cases: number
  executable_steps: number
  executable_assertions: number
  na_suites: number
  na_cases: number
  na_steps: number
  na_assertions: number
  overrides: number
}

export interface ExecutionPreview {
  profile_revision: number
  test_asset_revision: number
  profile: { id: number; name: string }
  release: { id: number | null; version: string }
  counts: PreviewCounts
  exclusion_preview: { target_type: string; path: string; reason_code: string | null; reason_note: string | null }[]
  warnings: unknown[]
  prepare_token: string | null
  prepare_expires_at: string | null
}

export interface SkipTarget {
  type: 'suite' | 'case' | 'step' | 'assertion' | 'suite_step'
  suite_id?: number
  case_id?: number
  node_key?: string
}

export interface SkipBatchResultItem {
  index: number
  status: 'changed' | 'unchanged' | 'invalid'
  rule_id: number | null
  error?: string | null
}

export interface SkipBatchResponse {
  request_id: string | null
  revision_before: number
  revision_after: number
  changed: number
  unchanged: number
  results: SkipBatchResultItem[]
}

export interface ProfileRunParams {
  app_profile_id: number
  app_release_id: number
  expected_profile_revision: number
  expected_test_asset_revision: number
}

export interface ProfileOverrides {
  revision: number
  elements: { element_id: number; locator_type: string; locator_value: string | null; locator_config?: SmartLocatorConfig | null }[]
  variables: { name: string; value: string; description: string | null }[]
  nodes: { suite_id: number; case_id: number | null; node_type: 'step' | 'assertion' | 'suite_step'; node_key: string; patch: Record<string, unknown> }[]
}

// ---------- 档案 ----------

export function listAppProfiles(projectId: number, params?: { include_disabled?: boolean; keyword?: string }) {
  return request.get<AppProfileSummary[]>(`/projects/${projectId}/app-profiles`, { params })
}

export function createAppProfile(projectId: number, data: { name: string; code: string; description?: string; inherit_all?: boolean; request_id?: string }) {
  return request.post<AppProfileSummary>(`/projects/${projectId}/app-profiles`, data)
}

export function getAppProfile(id: number) {
  return request.get<AppProfileSummary>(`/app-profiles/${id}`)
}

export function updateAppProfile(id: number, data: { request_id?: string; expected_revision: number; name?: string; description?: string; status?: string; inherit_all?: boolean }) {
  return request.patch<AppProfileSummary>(`/app-profiles/${id}`, data)
}

export function deleteAppProfile(id: number, data: { request_id?: string; expected_revision: number }) {
  return request.delete<void>(`/app-profiles/${id}`, { data })
}

// ---------- 发布版本 ----------

export function listReleases(profileId: number, params?: { status?: string; page?: number; page_size?: number }) {
  return request.get<PageData<AppProfileRelease>>(`/app-profiles/${profileId}/releases`, { params })
}

export function createRelease(profileId: number, data: { version: string; build_number?: string; description?: string; request_id?: string }) {
  return request.post<AppProfileRelease>(`/app-profiles/${profileId}/releases`, data)
}

export function updateRelease(id: number, data: { version?: string; build_number?: string; description?: string; status?: string; request_id?: string }) {
  return request.patch<AppProfileRelease>(`/app-profile-releases/${id}`, data)
}

export function deleteRelease(id: number, data: { request_id?: string }) {
  return request.delete<void>(`/app-profile-releases/${id}`, { data })
}

// ---------- 跳过规则 ----------

export function skipRulesBatch(profileId: number, data: { request_id?: string; expected_revision: number; operation: 'skip' | 'restore'; reason?: { code: string; note?: string }; targets: SkipTarget[] }) {
  return request.post<SkipBatchResponse>(`/app-profiles/${profileId}/skip-rules/batch`, data)
}

// ---------- 覆盖 ----------

export function listProfileOverrides(profileId: number) {
  return request.get<ProfileOverrides>(`/app-profiles/${profileId}/overrides`)
}

export function upsertElementOverride(profileId: number, elementId: number, data: { request_id?: string; expected_revision: number; locator_type: string; locator_value: string | null; locator_config?: SmartLocatorConfig | null }) {
  return request.put<{ revision: number }>(`/app-profiles/${profileId}/element-overrides/${elementId}`, data)
}

export function restoreElementOverride(profileId: number, elementId: number, data: { request_id?: string; expected_revision: number }) {
  return request.delete<void>(`/app-profiles/${profileId}/element-overrides/${elementId}`, { data })
}

export function upsertVariableOverride(profileId: number, name: string, data: { request_id?: string; expected_revision: number; value: string; description?: string }) {
  return request.put<{ revision: number }>(`/app-profiles/${profileId}/variable-overrides/${name}`, data)
}

export function restoreVariableOverride(profileId: number, name: string, data: { request_id?: string; expected_revision: number }) {
  return request.delete<void>(`/app-profiles/${profileId}/variable-overrides/${name}`, { data })
}

export function upsertNodeOverride(profileId: number, suiteId: number, caseId: number, nodeType: 'step' | 'assertion', nodeKey: string, data: { request_id?: string; expected_revision: number; patch: Record<string, unknown> }) {
  return request.put<{ revision: number }>(`/app-profiles/${profileId}/node-overrides/${suiteId}/${caseId}/${nodeType}/${nodeKey}`, data)
}

export function restoreNodeOverride(profileId: number, suiteId: number, caseId: number, nodeType: 'step' | 'assertion', nodeKey: string, data: { request_id?: string; expected_revision: number }) {
  return request.delete<void>(`/app-profiles/${profileId}/node-overrides/${suiteId}/${caseId}/${nodeType}/${nodeKey}`, { data })
}

// ---------- 套件前后置步骤覆盖 ----------

export function upsertSuiteStepOverride(profileId: number, suiteId: number, nodeKey: string, data: { request_id?: string; expected_revision: number; patch: Record<string, unknown> }) {
  return request.put<{ revision: number }>(`/app-profiles/${profileId}/suite-step-overrides/${suiteId}/${nodeKey}`, data)
}

export function restoreSuiteStepOverride(profileId: number, suiteId: number, nodeKey: string, data: { request_id?: string; expected_revision: number }) {
  return request.delete<void>(`/app-profiles/${profileId}/suite-step-overrides/${suiteId}/${nodeKey}`, { data })
}

// ---------- 工作台 ----------

export function workspace(profileId: number, params?: { page?: number; page_size?: number; keyword?: string; effective_status?: string; reason_code?: string; sort_by?: string; sort_order?: string }) {
  return request.get<WorkspacePage>(`/app-profiles/${profileId}/workspace`, { params })
}

export function workspaceNodes(profileId: number, params: { parent_type: 'suite' | 'case'; parent_id: number; ancestor_suite_id?: number; page?: number; page_size?: number; include?: string }) {
  return request.get<PageData<ProfileNode>>(`/app-profiles/${profileId}/workspace/nodes`, { params })
}

export function suiteSteps(profileId: number, suiteId: number, params?: { phase?: 'suite_setup' | 'suite_teardown'; page?: number; page_size?: number }) {
  return request.get<PageData<ProfileNode>>(`/app-profiles/${profileId}/suite-steps/${suiteId}`, { params })
}

export function differences(profileId: number, params?: { type?: 'all' | 'skipped' | 'overridden'; target_type?: string; reason_code?: string; keyword?: string; page?: number; page_size?: number }) {
  return request.get<PageData<DifferenceRow>>(`/app-profiles/${profileId}/differences`, { params })
}

// ---------- 执行预检 ----------

export function previewExecution(data: {
  project_id: number
  target: { type: 'case' | 'suite' | 'batch'; ids: number[]; target_scope?: 'explicit' | 'profile_all' }
  app_profile_id: number
  app_release_id: number
  device_id?: number | null
  parameters?: Record<string, unknown>
}) {
  return request.post<ExecutionPreview>('/executions/preview', data)
}
