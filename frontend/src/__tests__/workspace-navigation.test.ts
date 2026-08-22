import { beforeEach, describe, expect, it, vi } from 'vitest'

const pushMock = vi.fn()
const replaceMock = vi.fn()
const backMock = vi.fn()
let historyBack: string | null = null
const routeMock = {
  params: {} as Record<string, string>,
  meta: { workspace: 'global' } as Record<string, unknown>,
}

vi.stubGlobal('window', {
  history: {
    get state() {
      return { back: historyBack }
    },
  },
})

vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => ({ push: pushMock, replace: replaceMock, back: backMock }),
}))

import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'

beforeEach(() => {
  pushMock.mockReset()
  replaceMock.mockReset()
  backMock.mockReset()
  routeMock.params = {}
  routeMock.meta = { workspace: 'global' }
  historyBack = null
})

describe('工作区导航', () => {
  it('全局入口继续生成兼容旧链接的全局详情路由', () => {
    const navigation = useWorkspaceNavigation()
    expect(navigation.executionDetail(10)).toEqual({
      name: 'ExecutionDetail',
      params: { executionId: 10 },
    })
    expect(navigation.reportDetail(20)).toEqual({ name: 'ReportDetail', params: { id: 20 } })
  })

  it('项目入口生成同项目的执行和报告地址', () => {
    routeMock.params = { projectId: '7' }
    routeMock.meta = { workspace: 'project' }
    const navigation = useWorkspaceNavigation()

    expect(navigation.executionList()).toEqual({ name: 'ProjectExecutions', params: { projectId: 7 } })
    expect(navigation.executionDetail(10)).toEqual({
      name: 'ProjectExecutionDetail',
      params: { projectId: 7, executionId: 10 },
    })
    expect(navigation.reportList()).toEqual({ name: 'ProjectReports', params: { projectId: 7 } })
    expect(navigation.reportDetail(20)).toEqual({
      name: 'ProjectReportDetail',
      params: { projectId: 7, reportId: 20 },
    })
  })

  it('项目 ID 与资源不一致时替换成真实所属项目', async () => {
    routeMock.params = { projectId: '7' }
    routeMock.meta = { workspace: 'project' }
    const navigation = useWorkspaceNavigation()

    await navigation.normalizeProjectDetail('execution', 10, 8)

    expect(replaceMock).toHaveBeenCalledWith({
      name: 'ProjectExecutionDetail',
      params: { projectId: 8, executionId: 10 },
    })
  })

  it('返回优先使用真实浏览历史，无历史时回退当前工作区列表', () => {
    historyBack = '/projects/7/overview'
    const withHistory = useWorkspaceNavigation()
    withHistory.back('execution')
    expect(backMock).toHaveBeenCalledOnce()

    historyBack = null
    routeMock.params = { projectId: '7' }
    routeMock.meta = { workspace: 'project' }
    const directLink = useWorkspaceNavigation()
    directLink.back('report')
    expect(pushMock).toHaveBeenCalledWith({ name: 'ProjectReports', params: { projectId: 7 } })
  })

  it('登录重定向历史不可作为业务详情的返回目标', () => {
    historyBack = '/login?redirect=/projects/7/executions/10'
    routeMock.params = { projectId: '7' }
    routeMock.meta = { workspace: 'project' }
    const navigation = useWorkspaceNavigation()

    navigation.back('execution')

    expect(backMock).not.toHaveBeenCalled()
    expect(pushMock).toHaveBeenCalledWith({ name: 'ProjectExecutions', params: { projectId: 7 } })
  })
})
