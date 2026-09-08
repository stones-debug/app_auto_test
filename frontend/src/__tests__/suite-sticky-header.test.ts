import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const suiteSource = readFileSync(resolve(process.cwd(), 'src/views/Suite.vue'), 'utf8')

describe('套件用例编排吸顶标题栏', () => {
  it('只给用例编排 header 添加专用 class，并保留添加用例入口', () => {
    const headers = [...suiteSource.matchAll(/<header class="([^"]+)">([\s\S]*?)<\/header>/g)]
    const stickyHeaders = headers.filter(([, classes]) => classes.includes('case-orchestration-head'))

    expect(stickyHeaders).toHaveLength(1)
    expect(stickyHeaders[0]?.[2]).toContain('用例编排')
    expect(stickyHeaders[0]?.[2]).toContain('添加用例')
    expect(headers.filter(([, classes]) => classes.includes('case-orchestration-head'))).toHaveLength(1)
  })

  it('吸顶规则有明确的顶部位置、层级和不透明卡片背景', () => {
    const rule = suiteSource.match(/\.case-orchestration-head\s*\{([\s\S]*?)\}/)?.[1] ?? ''

    expect(rule).toMatch(/position:\s*sticky/)
    expect(rule).toMatch(/top:\s*0/)
    expect(rule).toMatch(/z-index:\s*2/)
    expect(rule).toMatch(/background:\s*var\(--card-bg\)/)
    expect(rule).toMatch(/box-shadow:/)
  })
})
