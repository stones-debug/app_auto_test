import { beforeEach, describe, expect, it, vi } from 'vitest'

const requestGet = vi.hoisted(() => vi.fn())

vi.mock('@/utils/request', () => ({
  default: { get: requestGet },
}))

import { downloadReport } from '@/api/reports'

describe('报告下载', () => {
  const createObjectURL = vi.fn(() => 'blob:report')
  const revokeObjectURL = vi.fn()
  const click = vi.fn()
  const remove = vi.fn()
  const appendChild = vi.fn()

  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    requestGet.mockResolvedValue(new Blob(['report']))
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    vi.stubGlobal('document', {
      createElement: vi.fn(() => ({ href: '', download: '', click, remove })),
      body: { appendChild },
    })
    vi.stubGlobal('window', { setTimeout })
  })

  it('使用唯一下载地址并延迟释放 Blob URL', async () => {
    await downloadReport(673)

    expect(requestGet).toHaveBeenCalledTimes(1)
    const [url, config] = requestGet.mock.calls[0]
    expect(url).toMatch(/^\/reports\/673\/download\?download_ts=\d+-[a-z0-9]+$/)
    expect(config).toMatchObject({ responseType: 'blob', suppressGlobalError: true })
    expect(appendChild).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).not.toHaveBeenCalled()

    vi.advanceTimersByTime(59_999)
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report')
  })
})
