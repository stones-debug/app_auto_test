import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/AppProfile.vue'), 'utf8')

function column(label: string): string {
  return source.match(new RegExp(`label="${label}"[\\s\\S]*?</el-table-column>`))?.[0] ?? ''
}

describe('APP 档案工作台「变量」列', () => {
  it('独立成列，位于「原因」列之前', () => {
    expect(source).toContain('label="变量"')
    expect(source.indexOf('label="变量"')).toBeLessThan(source.indexOf('label="原因"'))
  })

  it('变量不再挂在用例名称旁边', () => {
    expect(column('名称')).not.toContain('caseVariables(')
    expect(column('变量')).toContain('caseVariables(')
  })

  it('竖排展示「变量名：变量值」', () => {
    const block = column('变量')
    expect(block).toContain('case-variable-list')
    expect(block).toContain('variableToken(variable.name)')
    expect(block).toContain('variableDisplayText(variable)')
  })

  it('编辑入口是编辑图标，点击进入编辑态（不再点值编辑）', () => {
    const block = column('变量')
    expect(source).toContain("import { Edit } from '@element-plus/icons-vue'")
    expect(block).toContain(':icon="Edit"')
    expect(block).toContain('@click.stop="beginVariableEdit(displayNode(row), variable)"')
    // 变量值只做展示，不绑定点击进入编辑
    expect(block).toMatch(/<span class="variable-value"[\s\S]*?>\{\{ variableDisplayText\(variable\) \}\}<\/span>/)
  })

  it('编辑态提供「保存 / 取消」，不做失焦自动保存', () => {
    const block = column('变量')
    expect(block).toContain('>保存</el-button>')
    expect(block).toContain('>取消</el-button>')
    expect(block).toContain('@click.stop="cancelVariableEdit"')
    expect(block).not.toContain('@blur')
    expect(source).not.toContain('@blur="commitVariableEdit')
  })

  it('被覆盖的变量后面带「恢复」按钮', () => {
    const block = column('变量')
    expect(block).toContain('variableOverrideState(variable).overridden')
    expect(block).toContain('restoreCaseVariable(displayNode(row), variable)')
    expect(block).toContain('>恢复</el-button>')
  })
})
