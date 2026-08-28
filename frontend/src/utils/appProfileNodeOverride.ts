export const NODE_OVERRIDE_FIELDS = [
  'element_id',
  'params',
  'parameters',
  'expected',
  'expected_value',
  'timeout',
  'wait_timeout',
  'max_swipes',
  'duration',
] as const

const NODE_OVERRIDE_FIELD_SET = new Set<string>(NODE_OVERRIDE_FIELDS)

type JsonObject = Record<string, unknown>

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as JsonObject)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
    return `{${entries.join(',')}}`
  }
  return JSON.stringify(value) ?? 'undefined'
}

export function buildEffectiveNodeOverride(source: JsonObject, patch?: JsonObject): JsonObject {
  return cloneJson({ ...source, ...(patch ?? {}) })
}

/**
 * 编辑器展示公共参数与已有覆盖合并后的有效值；保存时只发送相对公共参数发生变化的顶层字段。
 * 删除某个预填字段表示继续继承公共配置，因此不会生成删除补丁。
 */
export function buildNodeOverrideDiff(source: JsonObject, edited: JsonObject): JsonObject {
  return Object.fromEntries(
    Object.entries(edited)
      .filter(([key]) => NODE_OVERRIDE_FIELD_SET.has(key))
      .filter(([key, value]) => stableJson(value) !== stableJson(source[key]))
      .map(([key, value]) => [key, cloneJson(value)]),
  )
}

export function unsupportedNodeOverrideFields(edited: JsonObject): string[] {
  return Object.keys(edited).filter((key) => !NODE_OVERRIDE_FIELD_SET.has(key))
}
