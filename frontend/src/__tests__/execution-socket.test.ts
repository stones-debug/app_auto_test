import { describe, expect, it } from 'vitest'

import socketSource from '@/composables/useExecutionSocket.ts?raw'
import detailSource from '@/views/ExecutionDetail.vue?raw'

describe('执行详情服务端重启恢复', () => {
  it('执行 WebSocket 持续重连，并在恢复连接后重新拉取服务端状态', () => {
    expect(socketSource).toContain('retries: -1')
    expect(socketSource).toContain('onConnected?: () => void')
    expect(detailSource).toContain('resyncAfterConnect(id)')
  })
})
