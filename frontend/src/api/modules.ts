import request from '@/utils/request'

/**
 * 测试模块（树形）接口。
 *
 * `scope` 区分两棵互相独立的树：`case` 供用例使用、`suite` 供套件使用。
 * 模块写操作（含拖拽移动）由服务端校验 scope、循环、跨项目，前端只做体验层的预判。
 */
export type ModuleScope = 'case' | 'suite'

export interface TestModule {
  id: number
  project_id: number
  parent_id: number | null
  name: string
  sort_order: number
  scope: ModuleScope
  created_at: string
  updated_at: string
}

export interface ModuleListOptions {
  scope?: ModuleScope
  /** 只取某个父模块下的直接子模块；不传则返回该 scope 的全部模块（前端建树）。 */
  parentId?: number | null
  /** 只取根层级模块。 */
  rootOnly?: boolean
}

export function listModules(projectId: number, options: ModuleListOptions = {}) {
  const params: Record<string, unknown> = { scope: options.scope ?? 'case' }
  if (options.rootOnly) params.root_only = true
  else if (options.parentId != null) params.parent_id = options.parentId
  return request.get<TestModule[]>(`/projects/${projectId}/modules`, { params })
}

export function createModule(
  projectId: number,
  data: { name: string; parent_id?: number | null; scope?: ModuleScope; sort_order?: number },
) {
  return request.post<TestModule>(`/projects/${projectId}/modules`, data)
}

export function updateModule(
  id: number,
  data: { name?: string; parent_id?: number | null; sort_order?: number },
) {
  return request.put<TestModule>(`/modules/${id}`, data)
}

export function deleteModule(id: number) {
  return request.delete<void>(`/modules/${id}`)
}

/**
 * 拖拽移动：挂到 `parent_id` 下并排在 `before_id` 之前。
 * `parent_id=null` 表示根层级，`before_id=null` 表示追加到目标父级末尾。
 * 返回该 scope 的完整模块列表。
 */
export function moveModule(
  id: number,
  data: { parent_id: number | null; before_id: number | null },
) {
  return request.put<TestModule[]>(`/modules/${id}/position`, data)
}
