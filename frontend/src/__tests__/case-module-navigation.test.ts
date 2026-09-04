import { describe, expect, it } from 'vitest'

import { moduleKeyFromId, moduleQuery, parseModuleKey } from '@/utils/caseModuleNavigation'
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
