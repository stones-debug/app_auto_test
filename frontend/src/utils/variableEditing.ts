import type { Variable } from '@/api/suites'

/** 公共变量编辑时，敏感值必须由用户重新输入后才放入更新请求。 */
export function variableEditSeed(variable: Pick<Variable, 'value' | 'is_sensitive'>): string {
  return variable.is_sensitive ? '' : variable.value
}

/** 返回固定值更新字段；null 表示没有修改值，调用方仍可提交其它元数据。 */
export function buildVariableValueUpdate(
  original: Pick<Variable, 'value' | 'is_sensitive'>,
  draft: string,
  sensitiveValueChanged: boolean,
): { value: string } | null {
  if (original.is_sensitive && !sensitiveValueChanged) return null
  if (!original.is_sensitive && draft === original.value) return null
  return { value: draft }
}

/** 我的敏感变量不回填，因此保存空字符串也必须是一次真实覆盖。 */
export function buildMyVariableValueUpdate(draft: string): { value: string } {
  return { value: draft }
}

export function myVariableEditSeed(
  isSensitive: boolean,
  userValue: string | null,
  publicValue: string | null,
): string {
  return isSensitive ? '' : (userValue ?? publicValue ?? '')
}

export function buildRestoreVariableUpdate(): { value: null } {
  return { value: null }
}

export function shouldApplyVariableResponse(sequence: number, latestSequence: number): boolean {
  return sequence === latestSequence
}
