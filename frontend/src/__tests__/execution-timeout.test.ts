import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

import {
  DEFAULT_EXECUTION_TIMEOUT_SECONDS,
  MAX_EXECUTION_TIMEOUT_SECONDS,
} from '@/api/executions'

describe('execution timeout limits', () => {
  it('keeps the 1800-second default and exposes the 24-hour ceiling', () => {
    expect(DEFAULT_EXECUTION_TIMEOUT_SECONDS).toBe(1800)
    expect(MAX_EXECUTION_TIMEOUT_SECONDS).toBe(86400)
  })

  it('binds DevicePicker input to the shared maximum', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/DevicePicker.vue'), 'utf8')
    expect(source).toContain(':max="MAX_EXECUTION_TIMEOUT_SECONDS"')
    expect(source).toContain(':step="60"')
  })
})
