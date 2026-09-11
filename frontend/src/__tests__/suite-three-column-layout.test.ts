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

/** 去掉 CSS 注释，避免「被注释掉的规则」被当成生效规则 */
function activeStyle(source: string) {
  const style = source.split('<style')[1] ?? ''
  return style.replace(/\/\*[\s\S]*?\*\//g, '')
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

  it('左区固定占 42%，详情拿到剩余宽度', () => {
    // 42% 内含 16px 栏间距。这是有意放宽的取值，已超出最初「左区最多 1/3」的要求，
    // 若要改回 1/3 请同时修改 Suite.vue 的两处注释与本用例。
    expect(cssRule(suiteSource, '.suite-left')).toMatch(/flex:\s*0 0 calc\(42% - 16px\)/)

    // 左区内部：树占一半，列表吃掉剩余
    const tree = cssRule(suiteSource, '.suite-tree')
    expect(tree).toMatch(/flex:\s*50%/)

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

  it('窄屏回退目前停用：任何宽度下左区都是 42% 并排两列', () => {
    // Suite.vue 里那段 <1800px 的回退当前被整段注释掉，去注释后再判断是否真的有生效规则
    expect(activeStyle(suiteSource)).not.toMatch(/@media \(max-width: 1799px\)/)

    // 若将来重新启用，只允许调整左区内部：禁止 flex-wrap 整行下移，也禁止改动 .suite-main
    expect(suiteSource).not.toMatch(/\.suite-layout\s*\{[^}]*flex-wrap/)
    expect(suiteSource).not.toMatch(/\.suite-main\s*\{[^}]*flex:\s*1 1 100%/)
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
