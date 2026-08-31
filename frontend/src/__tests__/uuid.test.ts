import { afterEach, describe, expect, it, vi } from 'vitest'

import { createUuid } from '@/utils/uuid'

describe('createUuid', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('uses crypto.randomUUID when available', () => {
    const randomUUID = vi.fn().mockReturnValue('native-uuid')
    vi.stubGlobal('crypto', { randomUUID })

    expect(createUuid()).toBe('native-uuid')
    expect(randomUUID).toHaveBeenCalledOnce()
  })

  it('uses crypto.getRandomValues when randomUUID is unavailable', () => {
    vi.stubGlobal('crypto', {
      getRandomValues(bytes: Uint8Array) {
        bytes.fill(0)
        return bytes
      },
    })

    expect(createUuid()).toBe('00000000-0000-4000-8000-000000000000')
  })

  it('falls back to Math.random when Web Crypto is unavailable', () => {
    vi.stubGlobal('crypto', undefined)
    vi.spyOn(Math, 'random').mockReturnValue(0)

    expect(createUuid()).toBe('00000000-0000-4000-8000-000000000000')
  })
})
