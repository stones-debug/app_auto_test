import { computed } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'

export type WorkspaceDetailKind = 'execution' | 'report'
const NON_RETURNABLE_PATH = /^\/(?:login|403|404)(?:[/?#]|$)/

function positiveId(value: unknown): number | null {
  const id = Number(value)
  return Number.isInteger(id) && id > 0 ? id : null
}

/**
 * 全局/项目工作区唯一导航入口。
 *
 * 当前路由属于 project workspace 时生成项目级地址；全局入口继续生成旧地址。
 * 显式 projectOverride 只用于把错误的项目详情 URL 规范化到资源真实所属项目。
 */
export function useWorkspaceNavigation() {
  const route = useRoute()
  const router = useRouter()

  const projectId = computed(() => positiveId(route.params.projectId))
  const isProjectWorkspace = computed(
    () => route.meta.workspace === 'project' && projectId.value !== null,
  )

  function targetProjectId(projectOverride?: number): number | null {
    if (projectOverride !== undefined) return positiveId(projectOverride)
    return isProjectWorkspace.value ? projectId.value : null
  }

  function executionList(projectOverride?: number): RouteLocationRaw {
    const project = targetProjectId(projectOverride)
    return project
      ? { name: 'ProjectExecutions', params: { projectId: project } }
      : { name: 'Executions' }
  }

  function executionDetail(executionId: number, projectOverride?: number): RouteLocationRaw {
    const project = targetProjectId(projectOverride)
    return project
      ? { name: 'ProjectExecutionDetail', params: { projectId: project, executionId } }
      : { name: 'ExecutionDetail', params: { executionId } }
  }

  function reportList(projectOverride?: number): RouteLocationRaw {
    const project = targetProjectId(projectOverride)
    return project
      ? { name: 'ProjectReports', params: { projectId: project } }
      : { name: 'Reports' }
  }

  function reportDetail(reportId: number, projectOverride?: number): RouteLocationRaw {
    const project = targetProjectId(projectOverride)
    return project
      ? { name: 'ProjectReportDetail', params: { projectId: project, reportId } }
      : { name: 'ReportDetail', params: { id: reportId } }
  }

  async function normalizeProjectDetail(
    kind: WorkspaceDetailKind,
    resourceId: number,
    actualProjectId: number,
  ): Promise<void> {
    if (!isProjectWorkspace.value || projectId.value === actualProjectId) return
    const target = kind === 'execution'
      ? executionDetail(resourceId, actualProjectId)
      : reportDetail(resourceId, actualProjectId)
    await router.replace(target)
  }

  function back(kind: WorkspaceDetailKind): void {
    const historyBack = typeof window !== 'undefined' ? window.history.state?.back : null
    if (
      typeof historyBack === 'string'
      && historyBack.startsWith('/')
      && !NON_RETURNABLE_PATH.test(historyBack)
    ) {
      router.back()
      return
    }
    void router.push(kind === 'execution' ? executionList() : reportList())
  }

  return {
    projectId,
    isProjectWorkspace,
    executionList,
    executionDetail,
    reportList,
    reportDetail,
    normalizeProjectDetail,
    back,
  }
}
