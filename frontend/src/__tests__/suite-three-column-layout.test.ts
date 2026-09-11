import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const suiteSource = readFileSync(resolve(process.cwd(), 'src/views/Suite.vue'), 'utf8')
const caseSource = readFileSync(resolve(process.cwd(), 'src/views/Case.vue'), 'utf8')

/** 取出源码里第一个同名 CSS 规则的声明体（媒体查询内的同名规则排在后面，不会被取到） */
function cssRule(source: string, selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return source.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\}`))?.[1] ?? ''
}

/** 按给定顺序查找锚点，返回各自下标；找不到时直接失败 */
function indexesOf(source: string, anchors: string[]) {
  return anchors.map((anchor) => {
    const index = source.indexOf(anchor)
    expect(index, `未找到锚点：${anchor}`).toBeGreaterThan(-1)
    return index
  })
}

describe('套件页三栏布局（左区 │ 套件详情）', () => {
  it('左区容纳模块树与套件列表，视觉上仍是 树 → 列表 → 详情 三栏', () => {
    const [layout, left, tree, sidebar, asideEnd, main] = indexesOf(suiteSource, [
      '<div class="suite-layout">',
      '<div class="suite-left">',
      'class="suite-tree"',
      '<aside class="suite-sidebar">',
      '</aside>',
      '<div class="suite-main">',
    ])

    expect(left).toBeGreaterThan(layout)
    expect(tree).toBeGreaterThan(left)
    expect(sidebar).toBeGreaterThan(tree)
    expect(asideEnd).toBeGreaterThan(sidebar)
    expect(main).toBeGreaterThan(asideEnd)

    // 模块树与列表都直接挂在 .suite-left 下，不再内嵌进列表卡片
    expect(suiteSource).not.toContain('sidebar-body')
    expect(suiteSource).not.toContain('sidebar-modules')
    expect(suiteSource).not.toContain(':deep(.module-tree)')

    // 与用例页同构：ModuleTree 直接作为布局第一列
    expect(caseSource).toContain('<ModuleTree')
    expect(caseSource).toContain('scope="case"')
    expect(suiteSource).toMatch(/scope="suite"/)
  })

  it('左区合计不超过页面 1/3，详情拿到剩余 2/3', () => {
    // 33.333% 内含 16px 栏间距，保证「左区 + 间距」正好是 1/3
    expect(cssRule(suiteSource, '.suite-left')).toMatch(/flex:\s*0 0 calc\(33\.333% - 16px\)/)

    // 左区内部：树是固定宽的窄导航列，列表吃掉剩余
    const tree = cssRule(suiteSource, '.suite-tree')
    expect(tree).toMatch(/flex:\s*0 0 164px/)

    const sidebar = cssRule(suiteSource, '.suite-sidebar')
    expect(sidebar).toMatch(/flex:\s*1 1 auto/)
    expect(sidebar).toMatch(/min-width:\s*0/)

    const main = cssRule(suiteSource, '.suite-main')
    expect(main).toMatch(/flex:\s*1/)
    expect(main).toMatch(/min-width:\s*0/)
  })

  it('左区两列吸顶：layout 不设 align-items（否则 .suite-left 不撑满行高，sticky 失效）', () => {
    const layout = cssRule(suiteSource, '.suite-layout')
    expect(layout).toMatch(/display:\s*flex/)
    expect(layout).not.toMatch(/flex-direction:\s*column/)
    expect(layout).not.toMatch(/align-items/)

    expect(cssRule(suiteSource, '.suite-left')).toMatch(/align-items:\s*flex-start/)
    expect(cssRule(suiteSource, '.suite-tree')).toMatch(/position:\s*sticky/)
    expect(cssRule(suiteSource, '.suite-sidebar')).toMatch(/position:\s*sticky/)
  })

  it('窄屏（<1800px）左区内部转为上下结构，左区占比不变', () => {
    const media = suiteSource.match(/@media \(max-width: 1799px\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''

    expect(media).toBeTruthy()
    expect(media).toMatch(/\.suite-left\s*\{\s*flex-direction:\s*column/)

    // 竖排后 flex-basis 变成高度：树必须重置回内容高度，并把宽度撑满左区
    const treeInStack = media.match(/\.suite-left \.suite-tree\s*\{([\s\S]*?)\}/)?.[1] ?? ''
    expect(treeInStack).toMatch(/flex:\s*0 0 auto/)
    expect(treeInStack).toMatch(/width:\s*100%/)

    expect(media).toMatch(/\.suite-sidebar\s*\{\s*flex:\s*0 0 auto/)

    // 回退只调整左区内部，不改变左区占比、也不整行下移
    expect(media).not.toMatch(/flex-wrap/)
    expect(media).not.toMatch(/\.suite-main/)
    expect(media).not.toMatch(/\.suite-left\s*\{[\s\S]*?flex:\s*1 1 100%/)
  })
})

describe('套件详情区不再有标题栏', () => {
  it('详情区不渲染套件名称、运行按钮与「套件操作」菜单', () => {
    expect(suiteSource).not.toContain('<PageHeader')
    expect(suiteSource).not.toContain('import PageHeader')
    expect(suiteSource).not.toContain('套件操作')
    // 菜单回调随标题栏一起移除，否则 noUnusedLocals 会报未使用
    expect(suiteSource).not.toContain('onSuiteMenu')
  })

  it('详情区以统计块开头，运行与编辑入口仍保留在左侧列表卡片上', () => {
    expect(suiteSource).toMatch(
      /<div v-loading="loadingDetail" class="detail-stack">\s*<div class="stat-pills">/,
    )
    expect(suiteSource).toMatch(/<RunButton :type="'suite'" :id="suite\.id"/)
    expect(suiteSource).toMatch(/@command="onItemMenu\(\$event, suite\)"/)
  })
})
