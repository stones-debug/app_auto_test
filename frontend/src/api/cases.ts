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

// CR-09：Registry 元数据驱动动态表单（与 Agent Action/Assertion Registry 对齐）
export interface ParamField {
  key: string
  label: string
  type: 'text' | 'number' | 'select' | 'switch'
  options?: { value: string; label: string }[]
  default?: string | number | boolean
  placeholder?: string
  required?: boolean
}

export interface ActionMeta {
  value: string
  label: string
  needsElement: boolean
  fields: ParamField[]
}

export interface AssertionMeta {
  value: string
  label: string
  needsElement: boolean
  fields: ParamField[]
}

export const ACTIONS: ActionMeta[] = [
  { value: 'launch_app', label: '启动 APP', needsElement: false, fields: [
    { key: 'package', label: '包名', type: 'text', required: true, placeholder: 'com.example.app' },
    { key: 'activity', label: 'Activity', type: 'text', placeholder: 'com.example.MainActivity' },
    { key: 'no_reset', label: '不重置', type: 'switch', default: true },
  ] },
  { value: 'close_app', label: '关闭 APP', needsElement: false, fields: [
    { key: 'package', label: '包名', type: 'text', placeholder: 'com.example.app' },
  ] },
  { value: 'click', label: '点击元素', needsElement: true, fields: [
    { key: 'wait_timeout', label: '等待秒数', type: 'number', default: 10 },
  ] },
  { value: 'input', label: '输入文本', needsElement: true, fields: [
    { key: 'value', label: '文本', type: 'text', required: true, placeholder: '如 ${username}' },
    { key: 'clear_first', label: '先清空', type: 'switch', default: true },
  ] },
  { value: 'clear', label: '清空输入框', needsElement: true, fields: [] },
  { value: 'swipe', label: '滑动', needsElement: false, fields: [
    { key: 'direction', label: '方向', type: 'select', default: 'up', options: [
      { value: 'up', label: '上滑' }, { value: 'down', label: '下滑' },
      { value: 'left', label: '左滑' }, { value: 'right', label: '右滑' },
    ] },
    { key: 'duration', label: '时长(ms)', type: 'number', default: 500 },
  ] },
  { value: 'scroll', label: '滚动到元素', needsElement: true, fields: [] },
  { value: 'back', label: '返回键', needsElement: false, fields: [] },
  { value: 'sleep', label: '等待', needsElement: false, fields: [
    { key: 'duration', label: '秒数', type: 'number', default: 1 },
  ] },
  { value: 'screenshot', label: '截图', needsElement: false, fields: [
    { key: 'filename', label: '文件名', type: 'text', placeholder: '留空自动生成安全文件名' },
  ] },
  { value: 'get_text', label: '获取文本', needsElement: true, fields: [
    { key: 'variable_name', label: '存入变量', type: 'text', placeholder: '可选，如 text_1' },
  ] },
  { value: 'get_attribute', label: '获取属性', needsElement: true, fields: [
    { key: 'attribute', label: '属性名', type: 'text', required: true, placeholder: '如 content-desc' },
    { key: 'variable_name', label: '存入变量', type: 'text', placeholder: '可选，如 attr_1' },
  ] },
  { value: 'tap_coordinate', label: '坐标点击', needsElement: false, fields: [
    { key: 'x', label: 'X', type: 'number', required: true },
    { key: 'y', label: 'Y', type: 'number', required: true },
  ] },
]

export const ASSERTION_TYPES: AssertionMeta[] = [
  { value: 'element_exists', label: '元素存在/不存在', needsElement: true, fields: [
    { key: 'expected', label: '期望', type: 'select', default: 'exists', options: [
      { value: 'exists', label: '存在' }, { value: 'not_exists', label: '不存在' },
    ] },
  ] },
  { value: 'text_equals', label: '文本等于', needsElement: true, fields: [
    { key: 'expected', label: '期望值', type: 'text', required: true },
    { key: 'trim', label: '去空格', type: 'switch', default: false },
  ] },
  { value: 'text_contains', label: '文本包含', needsElement: true, fields: [
    { key: 'expected', label: '期望值', type: 'text', required: true },
  ] },
  { value: 'text_not_contains', label: '文本不包含', needsElement: true, fields: [
    { key: 'expected', label: '期望值', type: 'text', required: true },
  ] },
  { value: 'attribute_equals', label: '属性等于', needsElement: true, fields: [
    { key: 'attribute', label: '属性名', type: 'text', required: true },
    { key: 'expected', label: '期望值', type: 'text', required: true },
  ] },
  { value: 'attribute_contains', label: '属性包含', needsElement: true, fields: [
    { key: 'attribute', label: '属性名', type: 'text', required: true },
    { key: 'expected', label: '期望值', type: 'text', required: true },
  ] },
  { value: 'value_equals', label: '输入框值等于', needsElement: true, fields: [
    { key: 'expected', label: '期望值', type: 'text', required: true },
  ] },
  { value: 'regex_match', label: '正则匹配', needsElement: true, fields: [
    { key: 'pattern', label: '正则', type: 'text', required: true, placeholder: '如 ^\\d{4}$' },
  ] },
]

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