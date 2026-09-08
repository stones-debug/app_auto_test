import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const caseSource = readFileSync(resolve(process.cwd(), 'src/views/Case.vue'), 'utf8')
const elementSource = readFileSync(resolve(process.cwd(), 'src/views/Element.vue'), 'utf8')
const editorSource = readFileSync(resolve(process.cwd(), 'src/views/CaseEditor.vue'), 'utf8')

describe('用例和元素列表分页契约', () => {
  it('两个列表都提供页大小和 jumper，并通过明确事件加载', () => {
    for (const source of [caseSource, elementSource]) {
      expect(source).toContain('layout="total, sizes, prev, pager, next, jumper"')
      expect(source).toContain('@current-change="onPageChange"')
      expect(source).toContain('@size-change="onPageSizeChange"')
      expect(source).not.toContain('@change="load"')
    }
    expect(caseSource).toContain(':page-sizes="[...CASE_LIST_PAGE_SIZES]"')
    expect(elementSource).toContain(':page-sizes="[20, 50, 100]"')
  })

  it('页大小变化重置到第一页并只从 size-change 入口加载', () => {
    expect(caseSource).toContain('pageSize.value = parseCaseListPageSize(nextPageSize)')
    expect(elementSource).toContain('pageSize.value = [20, 50, 100].includes(nextPageSize) ? nextPageSize : 20')
    expect(caseSource).toContain('page.value = 1\n  void load()')
    expect(elementSource).toContain('page.value = 1\n  void load()')
  })

  it('编辑页返回时传递 page_size，上下文非法值由导航工具回落默认值', () => {
    expect(editorSource).toContain('parseCaseListPageSize(route.query.page_size)')
    expect(editorSource).toContain('caseListQuery(moduleKey, returnPage, returnPageSize)')
  })

  it('元素列表刷新后校正越界页，筛选条件仍重置第一页', () => {
    expect(elementSource).toContain('const correctedPage = Math.min(page.value, lastPage)')
    expect(elementSource).toContain('function resetPageAndLoad()')
    expect(elementSource).toContain('@clear="resetPageAndLoad"')
    expect(elementSource).toContain('@change="resetPageAndLoad"')
    expect(elementSource).toContain('selectedPage.value = name\n  page.value = 1')
  })
})
