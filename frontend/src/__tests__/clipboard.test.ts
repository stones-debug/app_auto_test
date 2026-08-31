import { afterEach, describe, expect, it, vi } from 'vitest'

import { copyText } from '@/utils/clipboard'

describe('copyText', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    delete (globalThis as Record<string, unknown>).navigator
    delete (globalThis as Record<string, unknown>).document
  })

  it('uses the async clipboard API when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })

    await copyText('agent-key')

    expect(writeText).toHaveBeenCalledWith('agent-key')
  })

  it('falls back to execCommand when the async API is unavailable', async () => {
    const textarea = {
      value: '',
      style: {} as CSSStyleDeclaration,
      setAttribute: vi.fn(),
      select: vi.fn(),
      remove: vi.fn(),
    }
    const documentMock = {
      body: { appendChild: vi.fn() },
      documentElement: { appendChild: vi.fn() },
      createElement: vi.fn().mockReturnValue(textarea),
      execCommand: vi.fn().mockReturnValue(true),
    }
    vi.stubGlobal('document', documentMock)

    await copyText('agent-key')

    expect(documentMock.createElement).toHaveBeenCalledWith('textarea')
    expect(textarea.value).toBe('agent-key')
    expect(textarea.select).toHaveBeenCalled()
    expect(documentMock.execCommand).toHaveBeenCalledWith('copy')
    expect(textarea.remove).toHaveBeenCalled()
  })

  it('throws when neither clipboard mechanism is available', async () => {
    await expect(copyText('agent-key')).rejects.toThrow('Clipboard is unavailable')
  })
})
