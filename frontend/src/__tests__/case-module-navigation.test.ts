import { describe, expect, it } from 'vitest'

import {
  caseListQuery,
  moduleKeyFromId,
  moduleQuery,
  parseCaseListPage,
  parseCaseListPageSize,
  parseModuleKey,
} from '@/utils/caseModuleNavigation'
import { parseSuiteId, parseSuiteReturnId, suiteLocation } from '@/utils/suiteNavigation'

describe('case module navigation', () => {
  it('uses the persisted module, including the ungrouped module', () => {
    expect(moduleKeyFromId(12)).toBe('12')
    expect(moduleKeyFromId(null)).toBe('none')
    expect(moduleQuery(moduleKeyFromId(null))).toEqual({ module: 'none' })
  })

  it('keeps the previous list context for a new case', () => {
    expect(parseModuleKey('7')).toBe('7')
    expect(moduleQuery(parseModuleKey('7'))).toEqual({ module: '7' })
    expect(moduleQuery(parseModuleKey(undefined))).toEqual({})
  })

  it('falls back to all for invalid module query values', () => {
    expect(parseModuleKey('invalid')).toBe('all')
    expect(parseModuleKey('0')).toBe('all')
    expect(parseModuleKey(['none', '7'])).toBe('none')
  })

  it('preserves and validates the case list page context', () => {
    expect(parseCaseListPage('2')).toBe(2)
    expect(parseCaseListPage(['2', '3'])).toBe(2)
    expect(parseCaseListPage('0')).toBe(1)
    expect(parseCaseListPage('2.5')).toBe(1)
    expect(parseCaseListPage(undefined)).toBe(1)
    expect(parseCaseListPageSize('20')).toBe(20)
    expect(parseCaseListPageSize('50')).toBe(50)
    expect(parseCaseListPageSize(100)).toBe(100)
    expect(parseCaseListPageSize('25')).toBe(20)
    expect(parseCaseListPageSize(['100', '20'])).toBe(100)
    expect(parseCaseListPageSize(undefined)).toBe(20)
    expect(caseListQuery(parseModuleKey('7'), 2)).toEqual({ module: '7', page: '2' })
    expect(caseListQuery(parseModuleKey('7'), 1)).toEqual({ module: '7' })
    expect(caseListQuery(parseModuleKey('7'), 3, 100)).toEqual({ module: '7', page: '3', page_size: '100' })
    expect(caseListQuery(parseModuleKey('7'), 1, 50)).toEqual({ module: '7', page_size: '50' })
    expect(caseListQuery(parseModuleKey('all'), 2)).toEqual({ page: '2' })
    expect(caseListQuery(parseModuleKey(undefined), 2)).toEqual({ page: '2' })
  })

  it('preserves the suite context when returning from case editing', () => {
    expect(parseSuiteReturnId({ return_to: 'suite', suite_id: '12' })).toBe(12)
    expect(suiteLocation(3, 12)).toEqual({ path: '/projects/3/suites', query: { suite_id: '12' } })
  })

  it('rejects an invalid suite context', () => {
    expect(parseSuiteReturnId({ return_to: 'cases', suite_id: '12' })).toBeNull()
    expect(parseSuiteReturnId({ return_to: 'suite', suite_id: '0' })).toBeNull()
    expect(parseSuiteId(['12', '13'])).toBe(12)
  })
})
