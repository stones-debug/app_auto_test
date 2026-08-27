import request from '@/utils/request'
import type { PageData } from './projects'

export interface TestModule {
  id: number
  project_id: number
  parent_id: number | null
  name: string
  sort_order: number
  created_at: string
  updated_at: string
}

export interface TestElement {
  id: number
  project_id: number
  project_name?: string | null
  name: string
  page_name?: string | null
  platform?: string | null
  /** 适用范围：自由文本；all 表示所有；空/不填由服务端兜底为 all */
  scope: string
  locator_type: string
  locator_value: string
  description?: string | null
  created_by?: number | null
  created_by_name?: string | null
  created_at: string
  updated_at: string
}

export interface ElementGroup {
  id: number
  name: string
  created_by?: number | null
  created_at: string
}

export const LOCATOR_TYPES = [
  { value: 'id', label: 'ID' },
  { value: 'resource_id', label: 'Resource ID (Android)' },
  { value: 'xpath', label: 'XPath' },
  { value: 'accessibility_id', label: 'Accessibility ID' },
  { value: 'class_name', label: 'Class Name' },
  { value: 'uiautomator', label: 'UI Automator (Android)' },
  { value: 'predicate', label: 'Predicate (iOS)' },
  { value: 'coordinate', label: '坐标' },
  { value: 'custom', label: '自定义' },
]

export interface ElementPageCount {
  page_name: string
  count: number
  group_id?: number | null
}

export interface ElementUsage {
  case_id: number
  case_name: string
  step_orders?: number[]
}

export function listModules(projectId: number, parentId?: number | null) {
  return request.get<TestModule[]>(`/projects/${projectId}/modules`, {
    params: parentId === undefined ? {} : { parent_id: parentId ?? 0 },
  })
}

export function createModule(projectId: number, data: Partial<TestModule>) {
  return request.post<TestModule>(`/projects/${projectId}/modules`, data)
}

export function updateModule(id: number, data: Partial<TestModule>) {
  return request.put<TestModule>(`/modules/${id}`, data)
}

export function deleteModule(id: number) {
  return request.delete<void>(`/modules/${id}`)
}

export function listElements(
  params?: {
    page?: number
    page_size?: number
    keyword?: string
    platform?: string
    page_name?: string
    project_id?: number
    locator_type?: string
  },
) {
  return request.get<PageData<TestElement>>('/elements', { params })
}

export function elementPages() {
  return request.get<ElementPageCount[]>('/elements/pages')
}

export function createElement(data: Partial<TestElement>) {
  return request.post<TestElement>('/elements', data)
}

export function copyElement(id: number) {
  return request.post<TestElement>(`/elements/${id}/copy`)
}

export function createElementGroup(name: string) {
  return request.post<ElementGroup>('/elements/groups', { name })
}

export function deleteElementGroup(groupId: number) {
  return request.delete<void>(`/elements/groups/${groupId}`)
}

export function getElement(id: number) {
  return request.get<TestElement>(`/elements/${id}`)
}

export function updateElement(id: number, data: Partial<TestElement>) {
  return request.put<TestElement>(`/elements/${id}`, data)
}

export function deleteElement(id: number) {
  return request.delete<void>(`/elements/${id}`)
}

export function elementUsage(id: number) {
  return request.get<ElementUsage[]>(`/elements/${id}/usage`)
}

export function elementPageFilter(pageName: string): string | undefined {
  return pageName === 'all' ? undefined : pageName
}

export function totalElementCount(pages: ElementPageCount[]): number {
  return pages.reduce((sum, page) => sum + page.count, 0)
}
