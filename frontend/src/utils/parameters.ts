export type ParameterMap = Record<string, unknown> | null | undefined

export function hasParameters(parameters: ParameterMap): boolean {
  return parameters != null && Object.keys(parameters).length > 0
}

export function formatParameters(parameters: ParameterMap, pretty = false): string {
  if (!hasParameters(parameters)) return ''
  return JSON.stringify(parameters, null, pretty ? 2 : 0)
}
