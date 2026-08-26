import { actionMeta, type ParamField, type Step } from '@/api/cases'

function displayValue(field: ParamField, value: unknown): string {
  if (value == null || value === '') return ''
  if (field.type === 'switch') return value ? '是' : '否'
  if (field.type === 'select') {
    return field.options?.find((option) => option.value === value)?.label ?? String(value)
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function stepActionLabel(step: Step): string {
  return actionMeta(step.action).label
}

/** 元素 id → 名称映射；缺失或未加载时回退显示编号。 */
export type ElementNameMap = Map<number, string>

/** 生成步骤收起后的一行摘要，不包含任何仅供 UI 使用的字段。 */
export function stepSummaryText(step: Step, elementNames?: ElementNameMap): string {
  const meta = actionMeta(step.action)
  const details: string[] = []

  if (step.continue_on_failure) details.push('失败后继续')

  if (meta.needsElement) {
    if (step.element_id == null) {
      details.push('元素：未选择')
    } else {
      const name = elementNames?.get(step.element_id)
      details.push(name ? `元素：${name}` : `元素 #${step.element_id}`)
    }
  }

  for (const field of meta.fields) {
    const value = displayValue(field, step.params?.[field.key])
    if (value) details.push(`${field.label}：${value}`)
  }

  const description = step.description?.trim()
  if (description) details.push(description)

  return details.length ? details.join(' · ') : '无附加参数'
}
