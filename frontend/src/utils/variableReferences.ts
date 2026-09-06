const VARIABLE_PATTERN = /\$\{(\w+)\}/g

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
  override_template?: Record<string, unknown>
}): string[] {
  if (node.node_type !== 'step' && node.node_type !== 'suite_step') return []
  const template = node.override_template ?? {}
  const params = template.params ?? template.parameters
  return extractVariableReferences(params)
}
