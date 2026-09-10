/**
 * 模块筛选的三态契约（用例页与套件页共用）：
 *
 * - `all`  → 不带任何模块参数（后端不按模块过滤）
 * - `none` → `ungrouped=true`（后端按 `module_id IS NULL` 过滤）
 * - `<id>` → `module_id=<id>`（后端会包含该模块的子孙模块）
 *
 * 注意：**不能**用 `module_id=null` 表达未分组——axios 会直接丢弃值为 null 的
 * 查询参数，参数根本发不出去，后端 `None` 又表示“不过滤”，结果就是“未分组”变成
 * “全部”。历史上的用例列表就是这样失效的。
 */
export type ModuleKey = 'all' | 'none' | `${number}`

export function moduleKeyFromId(moduleId: number | null | undefined): ModuleKey {
  return moduleId == null ? 'none' : (String(moduleId) as `${number}`)
}

export function parseModuleKey(raw: unknown): ModuleKey {
  const value = Array.isArray(raw) ? raw[0] : raw
  if (value === 'none') return 'none'
  if (value === 'all' || value == null || value === '') return 'all'

  const moduleId = Number(value)
  return Number.isInteger(moduleId) && moduleId > 0 ? (String(moduleId) as `${number}`) : 'all'
}

/** 页面 URL query（走 `module` 参数，刷新/返回时保持筛选上下文）。 */
export function moduleQuery(key: ModuleKey): Record<string, string> {
  return key === 'all' ? {} : { module: key }
}

export interface ModuleFilterParams {
  module_id?: number
  ungrouped?: boolean
}

/** 列表接口的模块筛选参数。 */
export function moduleFilterParams(key: ModuleKey): ModuleFilterParams {
  if (key === 'none') return { ungrouped: true }
  if (key === 'all') return {}
  return { module_id: Number(key) }
}
