import { describe, expect, it } from 'vitest'

import {
  LOCATOR_TYPES,
  downloadElementImportTemplate,
  elementPageFilter,
  exportElements,
  importElements,
  totalElementCount,
} from '@/api/elements'

describe('元素库页面分组', () => {
  it('全部计数来自分组全量统计，不随当前列表筛选总数变化', () => {
    expect(
      totalElementCount([
        { page_name: '登录页', count: 3 },
        { page_name: '未分组', count: 2 },
        { page_name: '空自定义分组', count: 0, group_id: 9 },
      ]),
    ).toBe(5)
  })

  it('未分组作为明确筛选值发送，只有全部不发送 page_name', () => {
    expect(elementPageFilter('all')).toBeUndefined()
    expect(elementPageFilter('未分组')).toBe('未分组')
    expect(elementPageFilter('登录页')).toBe('登录页')
  })

  it('提供 Android Resource ID 定位方式', () => {
    expect(LOCATOR_TYPES).toContainEqual({
      value: 'resource_id',
      label: 'Resource ID (Android)',
    })
  })

  it('提供 Excel 模板下载、筛选导出和项目内导入 API', () => {
    expect(typeof downloadElementImportTemplate).toBe('function')
    expect(typeof exportElements).toBe('function')
    expect(typeof importElements).toBe('function')
  })
})
