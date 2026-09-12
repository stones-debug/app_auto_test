import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { buildMyVariableValueUpdate, buildRestoreVariableUpdate, buildVariableValueUpdate, myVariableEditSeed, shouldApplyVariableResponse } from '@/utils/variableEditing'

const apiSource = readFileSync(resolve(process.cwd(), 'src/api/appProfiles.ts'), 'utf8')
const drawerSource = readFileSync(resolve(process.cwd(), 'src/components/MyVariablesDrawer.vue'), 'utf8')
const caseApiSource = readFileSync(resolve(process.cwd(), 'src/api/cases.ts'), 'utf8')
const caseEditorSource = readFileSync(resolve(process.cwd(), 'src/views/CaseEditor.vue'), 'utf8')
const devicePickerSource = readFileSync(resolve(process.cwd(), 'src/components/DevicePicker.vue'), 'utf8')
const variableApiSource = readFileSync(resolve(process.cwd(), 'src/api/suites.ts'), 'utf8')
const caseVariableSource = readFileSync(resolve(process.cwd(), 'src/components/CaseVariableManager.vue'), 'utf8')
const variableViewSource = readFileSync(resolve(process.cwd(), 'src/views/Variable.vue'), 'utf8')
const suiteViewSource = readFileSync(resolve(process.cwd(), 'src/views/Suite.vue'), 'utf8')

describe('我的变量配置前端契约', () => {
  it('API 使用稳定 variable_id、分页筛选和 PATCH endpoint', () => {
    expect(apiSource).toContain('export interface MyVariableItem')
    expect(apiSource).toContain('variable_id: number')
    expect(apiSource).toContain('export function listMyVariables(')
    expect(apiSource).toContain('export function patchMyVariables(')
    expect(apiSource).toContain('/my-variables')
    expect(apiSource).toContain('scope?: MyVariableScope')
    expect(apiSource).toContain('overridden_only?: boolean')
  })

  it('敏感值不回填，编辑使用 password，保存/恢复各生成新的 request_id', () => {
    expect(drawerSource).toContain('myVariableEditSeed(item.is_sensitive, item.user_value, item.public_value)')
    expect(drawerSource).toContain(":type=\"variableRow(row).is_sensitive ? 'password' : 'text'\"")
    expect(drawerSource).toContain('request_id: createUuid()')
    expect(drawerSource).toContain('buildRestoreVariableUpdate()')
    expect(drawerSource).toContain('let requestSequence = 0')
    expect(drawerSource).toContain('shouldApplyVariableResponse(sequence, requestSequence)')
  })

  it('敏感编辑决策可执行验证：空值可保存，未重输公共敏感值不被覆盖', () => {
    expect(myVariableEditSeed(true, null, null)).toBe('')
    expect(myVariableEditSeed(false, null, 'public')).toBe('public')
    expect(buildMyVariableValueUpdate('')).toEqual({ value: '' })
    expect(buildVariableValueUpdate({ value: 'masked', is_sensitive: true }, '', false)).toBeNull()
    expect(buildVariableValueUpdate({ value: 'masked', is_sensitive: true }, '', true)).toEqual({ value: '' })
    expect(buildVariableValueUpdate({ value: 'public', is_sensitive: false }, 'public', false)).toBeNull()
    expect(buildRestoreVariableUpdate()).toEqual({ value: null })
  })

  it('列表竞态决策可执行验证：旧序号响应不能覆盖新筛选结果', () => {
    expect(shouldApplyVariableResponse(1, 2)).toBe(false)
    expect(shouldApplyVariableResponse(2, 2)).toBe(true)
  })

  it('用例请求不再携带内联 variables', () => {
    expect(caseApiSource).not.toMatch(/\n\s*variables: Record<string, unknown>/)
    expect(caseEditorSource).not.toContain('collectVariables')
    expect(caseEditorSource).not.toContain('variables:')
    expect(caseEditorSource).toContain('CaseVariableManager')
    expect(caseVariableSource).toContain("scope: 'case'")
    expect(caseVariableSource).toContain('case_id: props.caseId')
    expect(caseVariableSource).toContain('is_sensitive: form.value.is_sensitive')
    expect(variableApiSource).toContain('export function listVariables(')
    expect(variableApiSource).toContain('case_id?: number')
    expect(variableApiSource).toContain('is_sensitive?: boolean')
    expect(variableViewSource).toContain('is_sensitive: boolean')
    expect(variableViewSource).toContain('buildVariableValueUpdate')
    expect(variableViewSource).toContain('sensitiveValueChanged')
    expect(suiteViewSource).toContain('is_sensitive: variableForm.value.is_sensitive')
    expect(suiteViewSource).toContain('varEditing.is_sensitive')
  })

  it('运行预检与创建均保留 context_suite_case_id', () => {
    expect(apiSource).toContain('context_suite_case_id?: number | null')
    expect(devicePickerSource).toContain('context_suite_case_id: contextSuiteCaseId.value')
    expect(devicePickerSource).toContain('target.contextSuiteCaseId')
  })
})
