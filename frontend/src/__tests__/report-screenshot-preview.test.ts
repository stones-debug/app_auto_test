import { describe, expect, it } from 'vitest'

import authenticatedImageSource from '@/components/AuthenticatedImage.vue?raw'
import caseCardSource from '@/components/ReportCaseCard.vue?raw'
import nodeTableSource from '@/components/ReportNodeTable.vue?raw'
import stepTableSource from '@/components/ReportStepTable.vue?raw'

describe('报告截图预览接线', () => {
  it('节点表在有截图时使用报告文件鉴权地址并开启预览', () => {
    expect(nodeTableSource).toContain('nodes: ReportNode[]; reportId: number')
    expect(nodeTableSource).toContain('v-if="row.screenshot"')
    expect(nodeTableSource).toContain('reportFileUrl(reportId, row.screenshot)')
    expect(nodeTableSource).toContain(':preview="true"')
  })

  it('无截图时节点表不创建图片，且用例卡片传递 reportId', () => {
    expect(nodeTableSource).toContain('v-if="row.screenshot"')
    expect(caseCardSource).toContain(':nodes="caseItem.nodes" :report-id="reportId"')
  })

  it('步骤表显式开启截图预览', () => {
    expect(stepTableSource).toContain('reportFileUrl(reportId, row.screenshot)')
    expect(stepTableSource).toContain(':preview="true"')
  })

  it('鉴权图片仅在加载完成且 preview=true 时提供预览源', () => {
    expect(authenticatedImageSource).toContain('preview?: boolean')
    expect(authenticatedImageSource).toContain('v-if="objectUrl"')
    expect(authenticatedImageSource).toContain(':preview-src-list="props.preview ? [objectUrl] : []"')
    expect(authenticatedImageSource).toContain(':preview-teleported="props.preview"')
    expect(authenticatedImageSource).toContain(':hide-on-click-modal="props.preview"')
  })
})
