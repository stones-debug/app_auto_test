import request from '@/utils/request'
export { apiErrorDetail } from '@/utils/request'
export type { ApiErrorDetail } from '@/utils/request'

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
  override_counts: Record<string, number>
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
  /** 用例节点的编排项身份（test_suite_cases.id），重复编排时用于区分 */
  suite_case_id?: number | null
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
  child_count?: number
  difference_count?: number
  has_children: boolean
  // 套件节点：前后置步骤计数（工作台树状展开用）
  setup_step_count?: number
  teardown_step_count?: number
  // 用例节点：完整变量列表（表内竖排展示与就地编辑）
  variable_count?: number
  variables?: ProfileCaseVariable[]
  updated_at?: string | null
}

export interface ProfileVariableReference {
  node_type: 'step' | 'assertion'
  node_key: string
  order: number | null
  node_name: string
  inherited_value: string | null
  override_enabled: boolean
  override_value: string
}

export interface ProfileCaseVariable {
  name: string
  reference_count: number
  /** mixed=同名变量在不同节点存在不同覆盖值 */
  status: string
  inherited_value: string | null
  inherited_scope: string | null
  references: ProfileVariableReference[]
}

export interface ProfileSuiteCaseVariables {
  profile_id: number
  profile_revision: number
  suite_case_id: number
  case_id: number
  case_name: string
  total: number
  variables: ProfileCaseVariable[]
}

export interface ProfileVariableUpdate {
  name: string
  /** null 表示删除当前 occurrence 上的变量覆盖 */
  value: string | null
}

export interface WorkspacePage {
  profile_revision: number
  test_asset_revision: number
  total: number
  execution_selectable_total: number
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

export type SkipTarget = {
  type: 'suite'
  suite_id: number
} | {
  type: 'case'
  suite_case_id: number
} | {
  type: 'step' | 'assertion'
  suite_case_id: number
  node_key: string
} | {
  type: 'suite_step'
  suite_id: number
  node_key: string
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

export type MyVariableScope = 'project' | 'suite' | 'case'

export interface MyVariableReference {
  suite_id: number | null
  suite_name: string | null
  suite_case_id: number | null
  case_id: number | null
  case_name: string | null
  node_type: 'action' | 'assertion'
  node_key: string
  node_name: string
  action: string | null
  type: string | null
  phase: string | null
  order: number | null
}

export interface MyVariableItem {
  variable_id: number
  name: string
  scope: MyVariableScope
  project_id: number | null
  suite_id: number | null
  suite_name: string | null
  case_id: number | null
  case_name: string | null
  public_value: string | null
  user_value: string | null
  display_value: string
  overridden: boolean
  reference_count: number
  references: MyVariableReference[]
  is_sensitive: boolean
}

export interface MyVariablesPage {
  total: number
  page: number
  page_size: number
  items: MyVariableItem[]
}

export interface MyVariableUpdate {
  variable_id: number
  /** null 恢复公共值；空字符串是合法个人值。 */
  value: string | null
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

// ---------- 当前用户变量 ----------

export function listMyVariables(
  profileId: number,
  params?: { keyword?: string; scope?: MyVariableScope; overridden_only?: boolean; page?: number; page_size?: number },
) {
  return request.get<MyVariablesPage>(`/app-profiles/${profileId}/my-variables`, { params })
}

export function patchMyVariables(profileId: number, data: { request_id: string; updates: MyVariableUpdate[] }) {
  return request.patch<MyVariablesPage>(`/app-profiles/${profileId}/my-variables`, data)
}

// ---------- 工作台 ----------

export function workspace(profileId: number, params?: { page?: number; page_size?: number; keyword?: string; effective_status?: string; reason_code?: string; sort_by?: string; sort_order?: string }) {
  return request.get<WorkspacePage>(`/app-profiles/${profileId}/workspace`, { params })
}

export function workspaceNodes(profileId: number, params: { parent_type: 'suite' | 'case'; parent_id: number; ancestor_suite_id?: number; suite_case_id?: number; page?: number; page_size?: number; include?: string }) {
  return request.get<PageData<ProfileNode>>(`/app-profiles/${profileId}/workspace/nodes`, { params })
}

/** 用例节点变量详情：按变量分组并列出引用节点。 */
export function profileSuiteCaseVariables(profileId: number, suiteCaseId: number) {
  return request.get<ProfileSuiteCaseVariables>(`/app-profiles/${profileId}/suite-cases/${suiteCaseId}/variables`)
}

/**
 * 批量写入 occurrence 级变量覆盖；服务端在单事务内合并更新、递增一次 revision。
 *
 * 响应体与 GET 同构：新版本号在 `profile_revision`（**没有** `revision` 字段，
 * 响应模型会丢弃服务端多余的 `revision`，取错字段会拿到 undefined 而静默失败）。
 */
export function patchProfileSuiteCaseVariables(
  profileId: number,
  suiteCaseId: number,
  data: { request_id?: string; expected_revision: number; updates: ProfileVariableUpdate[] },
) {
  return request.patch<ProfileSuiteCaseVariables>(
    `/app-profiles/${profileId}/suite-cases/${suiteCaseId}/variable-overrides`,
    data,
  )
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
  target: { type: 'case' | 'suite' | 'batch'; ids: number[]; target_scope?: 'explicit' | 'profile_all'; excluded_suite_ids?: number[] }
  app_profile_id: number
  app_release_id: number
  device_id?: number | null
  parameters?: Record<string, unknown>
  context_suite_case_id?: number | null
}) {
  return request.post<ExecutionPreview>('/executions/preview', data)
}
