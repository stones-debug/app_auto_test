const VARIABLE_PATTERN = /\$\{([\p{L}\p{N}_]+)\}/gu

export function extractVariableReferences(value: unknown): string[] {
  const names: string[] = []
  const seen = new Set<string>()

  function visit(item: unknown) {
    if (typeof item === 'string') {
      for (const match of item.matchAll(VARIABLE_PATTERN)) {
        const name = match[1]
        if (!seen.has(name)) {
          seen.add(name)
          names.push(name)
        }
      }
    } else if (Array.isArray(item)) {
      item.forEach(visit)
    } else if (item !== null && typeof item === 'object') {
      Object.values(item as Record<string, unknown>).forEach(visit)
    }
  }

  visit(value)
  return names
}

export function stepVariableReferences(node: {
  node_type: string
  params?: Record<string, unknown>
  parameters?: Record<string, unknown>
  element_id?: unknown
}): string[] {
  if (node.node_type !== 'step' && node.node_type !== 'suite_step') return []
  const params = node.params ?? node.parameters
  return extractVariableReferences(params)
}

/** 动作与断言都参与变量引用（统一的快捷覆盖口径），套件前后置步骤同样适用。 */
export const VARIABLE_REFERENCE_NODE_TYPES = ['step', 'assertion', 'suite_step'] as const

/** 运行时输出变量名（如 get_text/get_attribute 的 variable_name），不是输入变量。 */
const OUTPUT_PARAM_KEYS = new Set(['variable_name'])

export function nodeVariableReferences(node: {
  node_type: string
  params?: Record<string, unknown>
  parameters?: Record<string, unknown>
  element_id?: unknown
}): string[] {
  if (!(VARIABLE_REFERENCE_NODE_TYPES as readonly string[]).includes(node.node_type)) return []
  const params = node.params ?? node.parameters
  if (params === null || typeof params !== 'object' || Array.isArray(params)) return []
  const filtered = Object.fromEntries(
    Object.entries(params as Record<string, unknown>).filter(([key]) => !OUTPUT_PARAM_KEYS.has(key)),
  )
  return extractVariableReferences(filtered)
}
