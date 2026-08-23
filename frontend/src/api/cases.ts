import request from '@/utils/request'
import type { PageData } from './projects'

// Step 10：Registry 元数据单一来源（generated from agent/executor/protocol_manifest.yaml）
import {
  ACTIONS,
  ASSERTION_TYPES,
  PROTOCOL_VERSION,
  type ActionMeta,
  type AssertionMeta,
  type ParamField,
} from './generated/registry'
export {
  ACTIONS,
  ASSERTION_TYPES,
  PROTOCOL_VERSION,
  type ActionMeta,
  type AssertionMeta,
  type ParamField,
}

export type StepPhase = 'setup' | 'main' | 'teardown'

export interface Step {
  order: number
  action: string
  phase?: StepPhase
  element_id?: number | null
  params?: Record<string, unknown>
  description?: string
  // Step 4：失败后继续为 Step 顶层字段（不入 params）
  continue_on_failure: boolean
}

export interface Assertion {
  order: number
  type: string
  element_id?: number | null
  params?: Record<string, unknown>
  description?: string
}

export interface TestCase {
  id: number
  project_id: number
  module_id: number | null
  name: string
  description?: string | null
  status: string
  steps: Step[]
  assertions: Assertion[]
  variables: Record<string, unknown>
  created_by?: number | null
  created_at: string
  updated_at: string
  // B3 扩展字段（列表项）
  module_name?: string | null
  step_count?: number
  assertion_count?: number
  last_execution_status?: string | null
  last_execution_at?: string | null
}

export const CASE_STATUS = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '启用' },
  { value: 'disabled', label: '禁用' },
]

// Step 10：ACTIONS/ASSERTION_TYPES 由 generated/registry.ts 提供（上方 re-export）

export function actionMeta(action: string): ActionMeta {
  return ACTIONS.find((a) => a.value === action) ?? ACTIONS[0]
}

export function assertionMeta(type: string): AssertionMeta {
  return ASSERTION_TYPES.find((a) => a.value === type) ?? ASSERTION_TYPES[0]
}

export function defaultParams(fields: ParamField[]): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  for (const f of fields) {
    if (f.default !== undefined) params[f.key] = f.default
  }
  return params
}

// Step 4：continue_on_failure 是 Step 顶层字段；加载旧数据/历史 params 时归一化，
// 并把历史遗留塞入 params 的同名字段剔除
export function normalizeStep(step: Step): Step {
  const { continue_on_failure: legacy, ...params } = (step.params ?? {}) as Record<string, unknown>
  void legacy
  return {
    ...step,
    phase: step.phase ?? 'main',
    params,
    continue_on_failure: step.continue_on_failure ?? false,
  }
}

export function listCases(
  projectId: number,
  params?: {
    page?: number
    page_size?: number
    module_id?: number | null
    keyword?: string
    status?: string
  },
) {
  return request.get<PageData<TestCase>>(`/projects/${projectId}/cases`, { params })
}

export function createCase(projectId: number, data: Partial<TestCase>) {
  return request.post<TestCase>(`/projects/${projectId}/cases`, data)
}

export function getCase(id: number) {
  return request.get<TestCase>(`/cases/${id}`)
}

export function updateCase(id: number, data: Partial<TestCase>) {
  return request.put<TestCase>(`/cases/${id}`, data)
}

export function deleteCase(id: number) {
  return request.delete<void>(`/cases/${id}`)
}

export function cloneCase(id: number) {
  return request.post<TestCase>(`/cases/${id}/clone`)
}
