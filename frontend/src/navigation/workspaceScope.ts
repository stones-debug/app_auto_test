export function withProjectScope<T extends Record<string, unknown>>(
  projectId: number | null,
  params: T,
): T & { project_id?: number } {
  if (projectId === null) return params
  return { ...params, project_id: projectId }
}
