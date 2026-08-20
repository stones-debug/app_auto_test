import request from '@/utils/request'
import type { PageData } from './projects'

export interface Step {
  order: number
  action: string
  element_id?: number | null
  params?: Record<string, unknown>
  description?: string
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
}

export const CASE_STATUS = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '启用' },
  { value: 'disabled', label: '禁用' },
]

export const ACTIONS = [
  { value: 'launch_app', label: '启动 APP', params: ['package', 'activity', 'no_reset'] },
  { value: 'close_app', label: '关闭 APP', params: ['package'] },
  { value: 'click', label: '点击元素', params: ['wait_timeout'] },
  { value: 'input', label: '输入文本', params: ['value', 'clear_first'] },
  { value: 'clear', label: '清空输入框', params: [] },
  { value: 'swipe', label: '滑动', params: ['direction', 'duration'] },
  { value: 'scroll', label: '滚动到元素', params: [] },
  { value: 'back', label: '返回键', params: [] },
  { value: 'sleep', label: '等待', params: ['duration'] },
  { value: 'screenshot', label: '截图', params: ['filename'] },
  { value: 'get_text', label: '获取文本', params: ['variable_name'] },
  { value: 'get_attribute', label: '获取属性', params: ['attribute', 'variable_name'] },
  { value: 'tap_coordinate', label: '坐标点击', params: ['x', 'y'] },
]

export const ASSERTION_TYPES = [
  { value: 'element_exists', label: '元素存在/不存在' },
  { value: 'text_equals', label: '文本等于' },
  { value: 'text_contains', label: '文本包含' },
  { value: 'text_not_contains', label: '文本不包含' },
  { value: 'attribute_equals', label: '属性等于' },
  { value: 'attribute_contains', label: '属性包含' },
  { value: 'value_equals', label: '输入框值等于' },
  { value: 'regex_match', label: '正则匹配' },
]

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