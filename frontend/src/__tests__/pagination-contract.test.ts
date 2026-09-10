import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * 分页「接线形状」契约。
 *
 * ⚠️ 本文件断言的是**源码结构**，不是运行时行为。真正的行为验证需要挂载组件
 * （需引入 @vue/test-utils + DOM 环境），仓库当前没有这两个依赖，因此这里只做
 * 低成本的结构回归。分页的运行时竞态/加载时序由
 * `use-list-query-race.test.ts` 以行为测试覆盖。
 *
 * 归一化是必须的：本仓库 `core.autocrlf=true`，Windows 检出为 CRLF、CI（Linux）
 * 为 LF。若直接 `toContain('...\n  ...')`，同一份代码在两端会得出相反结论，
 * 门禁就失去意义了。
 */
const normalize = (text: string) =>
  text
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .trim()

const read = (relativePath: string) =>
  normalize(readFileSync(resolve(process.cwd(), relativePath), 'utf8'))

const caseSource = read('src/views/Case.vue')
const elementSource = read('src/views/Element.vue')
const editorSource = read('src/views/CaseEditor.vue')

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
    expect(elementSource).toContain(
      'pageSize.value = [20, 50, 100].includes(nextPageSize) ? nextPageSize : 20',
    )
    expect(caseSource).toContain('page.value = 1\nvoid load()')
    expect(elementSource).toContain('page.value = 1\nvoid load()')
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
    expect(elementSource).toContain('selectedPage.value = name\npage.value = 1')
  })
})

describe('归一化本身', () => {
  it('CRLF 与 LF 得到同一结果', () => {
    expect(normalize('a\r\n  b')).toBe(normalize('a\n  b'))
  })

  it('缩进风格变化不影响匹配', () => {
    expect(normalize('function f() {\n    page.value = 1\n        void load()\n}')).toContain(
      'page.value = 1\nvoid load()',
    )
  })
})
