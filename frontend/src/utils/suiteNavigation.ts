/** 套件页与用例编辑页之间传递的返回上下文。 */
export function parseSuiteId(raw: unknown): number | null {
  const value = Array.isArray(raw) ? raw[0] : raw
  const suiteId = Number(value)
  return Number.isInteger(suiteId) && suiteId > 0 ? suiteId : null
}

export function parseSuiteReturnId(query: { return_to?: unknown; suite_id?: unknown }): number | null {
  const returnTo = Array.isArray(query.return_to) ? query.return_to[0] : query.return_to
  return returnTo === 'suite' ? parseSuiteId(query.suite_id) : null
}

export function suiteLocation(projectId: number, suiteId: number) {
  return {
    path: `/projects/${projectId}/suites`,
    query: { suite_id: String(suiteId) },
  }
}
