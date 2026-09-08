/** 用例列表与编辑页之间传递的模块筛选上下文。 */
export type CaseModuleKey = 'all' | 'none' | `${number}`

export function moduleKeyFromId(moduleId: number | null | undefined): CaseModuleKey {
  return moduleId == null ? 'none' : String(moduleId) as `${number}`
}

export function parseModuleKey(raw: unknown): CaseModuleKey {
  const value = Array.isArray(raw) ? raw[0] : raw
  if (value === 'none') return 'none'
  if (value === 'all' || value == null || value === '') return 'all'

  const moduleId = Number(value)
  return Number.isInteger(moduleId) && moduleId > 0 ? String(moduleId) as `${number}` : 'all'
}

export function moduleQuery(key: CaseModuleKey): Record<string, string> {
  return key === 'all' ? {} : { module: key }
}

export function parseCaseListPage(raw: unknown): number {
  const value = Array.isArray(raw) ? raw[0] : raw
  const page = typeof value === 'number' || typeof value === 'string' ? Number(value) : NaN
  return Number.isInteger(page) && page > 0 ? page : 1
}

export const CASE_LIST_PAGE_SIZES = [20, 50, 100] as const

export type CaseListPageSize = typeof CASE_LIST_PAGE_SIZES[number]

export function parseCaseListPageSize(raw: unknown): CaseListPageSize {
  const value = Array.isArray(raw) ? raw[0] : raw
  const pageSize = typeof value === 'number' || typeof value === 'string' ? Number(value) : NaN
  return CASE_LIST_PAGE_SIZES.includes(pageSize as CaseListPageSize)
    ? pageSize as CaseListPageSize
    : CASE_LIST_PAGE_SIZES[0]
}

export function caseListQuery(
  moduleKey: CaseModuleKey,
  page: number,
  pageSize: CaseListPageSize = CASE_LIST_PAGE_SIZES[0],
): Record<string, string> {
  const query = moduleQuery(moduleKey)
  if (page > 1) query.page = String(page)
  if (pageSize !== CASE_LIST_PAGE_SIZES[0]) query.page_size = String(pageSize)
  return query
}
