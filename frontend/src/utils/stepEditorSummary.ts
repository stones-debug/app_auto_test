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
  // Step 12：列表内滑动查找文字并点击 使用特化摘要，便于一眼看清目标与双向策略
  if (step.action === 'swipe_in_element_find_text_click') {
    const elementLabel = '列表'
    if (step.element_id == null) {
      details.push(`${elementLabel}：未选择`)
    } else {
      const name = elementNames?.get(step.element_id)
      details.push(name ? `${elementLabel}：${name}` : `${elementLabel} #${step.element_id}`)
    }
    const text = step.params?.['target_text'] as string | undefined
    const matchMode = step.params?.['match_mode'] === 'contains' ? '包含' : '等于'
    if (text) details.push(`文字${matchMode}“${text}”`)
    const preferred = step.params?.['preferred_direction'] === 'down' ? '下' : '上'
    const opposite = preferred === '上' ? '下' : '上'
    details.push(`自动${preferred}→${opposite}`)
    const maxSwipes = step.params?.['max_swipes_per_direction']
    if (maxSwipes != null) details.push(`每方向最多${maxSwipes}次`)
    const viewportId = step.params?.['viewport_element_id']
    if (viewportId != null && viewportId !== '') {
      const viewportName = elementNames?.get(Number(viewportId))
      details.push('视口：' + (viewportName ?? '#' + viewportId))
    }
    return details.join(' · ')
  }

  if (meta.needsElement) {
    if (step.element_id == null) {
      details.push('元素：未选择')
    } else {
      const name = elementNames?.get(step.element_id)
      details.push(name ? `元素：${name}` : `元素 #${step.element_id}`)
    }
  }

  for (const field of meta.fields) {
    const rawValue = step.params?.[field.key]
    const value = field.type === 'element'
      ? (rawValue == null || rawValue === '' ? '' : '#' + rawValue)
      : displayValue(field, rawValue)
    if (value) details.push(`${field.label}：${value}`)
  }

  const description = step.description?.trim()
  if (description) details.push(description)

  return details.length ? details.join(' · ') : '无附加参数'
}
