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
  // 方案 §2.8：稳定标识 UUID；新建前端生成，缺失由 normalizeStep 兜底
  key?: string
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
  key?: string
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

/** 在保存前执行 Registry 中可表达的轻量校验；服务端仍是最终校验方。 */
export function validateActionParams(action: string, params: Record<string, unknown> = {}): string | null {
  const meta = actionMeta(action)
  for (const field of meta.fields) {
    const value = params[field.key]
    if (field.required && (value === undefined || value === null || value === '')) {
      return `请填写“${field.label}”`
    }
    if (typeof value === 'string' && field.minLength !== undefined && value.trim().length < field.minLength) {
      return `“${field.label}”不能为空`
    }
    if (field.type !== 'number' || value === undefined || value === null || value === '') continue
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) return `“${field.label}”必须是数字`
    if (field.min !== undefined && numeric < field.min) return `“${field.label}”不能小于 ${field.min}`
    if (field.max !== undefined && numeric > field.max) return `“${field.label}”不能大于 ${field.max}`
  }
  for (const constraint of meta.constraints ?? []) {
    if (constraint.type !== 'percent_region') continue
    const left = Number(params[constraint.left])
    const top = Number(params[constraint.top])
    const width = Number(params[constraint.width])
    const height = Number(params[constraint.height])
    if (left + width > 100) return '左边界(%) + 宽度(%) 不能大于 100'
    if (top + height > 100) return '上边界(%) + 高度(%) 不能大于 100'
  }
  return null
}

/** 保存前统一校验一个步骤：需要元素时必带 element_id，再校验动作参数。 */
export function validateStep(step: Step): string | null {
  const meta = actionMeta(step.action)
  if (meta.needsElement && (step.element_id == null)) {
    return `请选择“${meta.elementLabel ?? '元素'}”`
  }
  return validateActionParams(step.action, step.params)
}

// Step 4：continue_on_failure 是 Step 顶层字段；加载旧数据/历史 params 时归一化，
// 并把历史遗留塞入 params 的同名字段剔除
// 方案 §2.8：步骤稳定 key 缺失/非法时兜底生成 UUID（后端同样兜底，读写一致）
export function normalizeStep(step: Step): Step {
  const { continue_on_failure: legacy, ...params } = (step.params ?? {}) as Record<string, unknown>
  void legacy
  return {
    ...step,
    key: normalizeNodeKey(step.key),
    phase: step.phase ?? 'main',
    params,
    continue_on_failure: step.continue_on_failure ?? false,
  }
}

function normalizeNodeKey(raw: string | undefined): string {
  if (raw) return raw
  return crypto.randomUUID()
}

export function normalizeAssertion(assertion: Assertion): Assertion {
  return { ...assertion, key: normalizeNodeKey(assertion.key) }
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

export interface CaseBatchDeleteResult {
  deleted: number
  deleted_count: number
  ids: number[]
}

export function deleteCases(projectId: number, ids: number[]) {
  return request.post<CaseBatchDeleteResult>(`/projects/${projectId}/cases/batch-delete`, { ids })
}


export function cloneCase(id: number) {
  return request.post<TestCase>(`/cases/${id}/clone`)
}
