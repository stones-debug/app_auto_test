import { describe, expect, it } from 'vitest'

import type { ReportDetail, ReportExclusion } from '@/api/reports'

describe('报告 N/A 契约（方案 §7）', () => {
  it('ReportSummary 含 not_applicable / exclusion_summary', () => {
    const summary: import('@/api/reports').ReportSummary = {
      id: 1,
      total: 10,
      passed: 8,
      failed: 1,
      error_count: 1,
      skipped: 0,
      success_rate: 80,
      not_applicable: 4,
      exclusion_summary: { na_cases: 4 },
    }
    expect(summary.not_applicable).toBe(4)
    expect(summary.exclusion_summary?.na_cases).toBe(4)
  })

  it('ReportDetail 可携带 exclusions（不适用内容清单）', () => {
    const exclusions: ReportExclusion[] = [
      {
        target_type: 'case',
        path: '登录/网约车司机登录',
        reason_code: 'unsupported',
        reason_note: 'DVR 无司机角色',
        source_type: 'direct',
      },
      { target_type: 'step', path: '登录/点击登录', reason_code: 'not_adapted', reason_note: null, source_type: 'inherited' },
    ]
    const detail = { exclusions } as ReportDetail
    expect(detail.exclusions).toHaveLength(2)
    expect(detail.exclusions?.[0].target_type).toBe('case')
    expect(detail.exclusions?.[1].source_type).toBe('inherited')
  })

  it('ReportListItem 含档案/版本与 N/A 数量', () => {
    const item: import('@/api/reports').ReportListItem = {
      id: 1,
      execution_id: 2,
      total: 5,
      passed: 4,
      failed: 1,
      error_count: 0,
      skipped: 0,
      success_rate: 80,
      has_report: true,
      created_at: '',
      project_id: 1,
      execution_status: 'passed',
      execution_type: 'suite',
      case_name: null,
      suite_name: 'S',
      app_profile_name: 'DVR',
      app_release_version: '2026.08.1',
      not_applicable: 2,
      duration: null,
    }
    expect(item.app_profile_name).toBe('DVR')
    expect(item.not_applicable).toBe(2)
  })
})
