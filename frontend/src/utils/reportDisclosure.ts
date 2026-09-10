import type { ReportCase, ReportSuite } from '@/api/reports'
import { splitExecutionSteps } from '@/utils/executionOrder'
export interface PreparedReportCase extends ReportCase {
  beforeAssertionSteps: ReportCase['steps']
  afterAssertionSteps: ReportCase['steps']
}

export interface PreparedReportSuite extends Omit<ReportSuite, 'cases'> {
  cases: PreparedReportCase[]
}

export interface ReportDisclosureState {
  expandedSuites: Set<number>
  expandedCases: Set<number>
}

const isFailed = (status: string) => ['failed', 'error'].includes(status)

export function prepareReportCase(reportCase: ReportCase): PreparedReportCase {
  // 类型上 steps 必填、后端也始终下发；但一旦缺字段，splitExecutionSteps 会在
  // `undefined.filter` 上抛错，而调用方在 ReportDetail.load 的 try 内 —— 结果是
  // 整份报告渲染失败、页面空白。畸形数据降级为空数组，不要把整页拖垮。
  const { beforeAssertions, afterAssertions } = splitExecutionSteps(reportCase.steps ?? [])
  return { ...reportCase, beforeAssertionSteps: beforeAssertions, afterAssertionSteps: afterAssertions }
}

export function prepareReportSuites(suites: ReportSuite[]): PreparedReportSuite[] {
  return suites.map((suite) => ({
    ...suite,
    cases: (suite.cases ?? []).map(prepareReportCase),
  }))
}

export function prepareReportCases(cases: ReportCase[]): PreparedReportCase[] {
  return cases.map(prepareReportCase)
}

/** 首次加载只展开包含失败/异常用例的套件，以及对应失败/异常用例。 */
export function initialDisclosureState(
  suites: Array<Pick<ReportSuite, 'id' | 'cases'>>,
  cases: Array<Pick<ReportCase, 'id' | 'status'>>,
): ReportDisclosureState {
  const expandedSuites = new Set<number>()
  const expandedCases = new Set<number>()

  if (suites.length) {
    for (const suite of suites) {
      // 入参可能是未经过 prepareReportSuites 的原始响应，cases 缺失时按空处理
      const failedCases = (suite.cases ?? []).filter((reportCase) => isFailed(reportCase.status))
      if (!failedCases.length) continue
      expandedSuites.add(suite.id)
      failedCases.forEach((reportCase) => expandedCases.add(reportCase.id))
    }
  } else {
    cases.filter((reportCase) => isFailed(reportCase.status)).forEach((reportCase) => {
      expandedCases.add(reportCase.id)
    })
  }

  return { expandedSuites, expandedCases }
}

/** 当前可见内容的展开状态：展开套件，套件内仍只自动展开失败/异常用例。 */
export function visibleDisclosureState(
  suites: Array<Pick<ReportSuite, 'id' | 'cases'>>,
  cases: Array<Pick<ReportCase, 'id' | 'status'>>,
): ReportDisclosureState {
  const expandedSuites = new Set(suites.map((suite) => suite.id))
  const sourceCases = suites.length
    ? suites.flatMap((suite) => suite.cases ?? [])
    : cases
  const expandedCases = new Set(
    sourceCases.filter((reportCase) => isFailed(reportCase.status)).map((reportCase) => reportCase.id),
  )
  return { expandedSuites, expandedCases }
}

export function toggleDisclosureId(current: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(current)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

export function emptyDisclosureState(): ReportDisclosureState {
  return {
    expandedSuites: new Set(),
    expandedCases: new Set(),
  }
}
