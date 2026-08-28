import { useWebSocket } from '@vueuse/core'
import { ref } from 'vue'

export function executionWsUrl(executionId: number, token: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/executions/${executionId}?token=${token}`
}

// Step 7：统一维护 connected/connecting/error 状态（页面不再本地复制一份）
export function useExecutionSocket(
  executionId: number,
  token: string,
  onMessage: (msg: Record<string, unknown>) => void,
  onConnected?: () => void,
) {
  const connected = ref(false)
  const connecting = ref(true)
  const error = ref<string | null>(null)
  const url = ref(executionWsUrl(executionId, token))
  let manuallyClosed = false
  const ws = useWebSocket(url, {
    autoReconnect: {
      // 服务端升级/重启可能超过 10 秒；执行未终态前持续重连，连接恢复后
      // ExecutionDetail 会立即通过 REST 补拉断线窗口中的状态和日志。
      retries: -1,
      delay: 1000,
      onFailed() {
        connecting.value = false
        error.value = '连接失败'
        // 重连失败不提示，前端以 REST 拉取兜底
      },
    },
    heartbeat: {
      message: JSON.stringify({ type: 'ping' }),
      interval: 30000,
      pongTimeout: 5000,
    },
    onConnected: () => {
      connected.value = true
      connecting.value = false
      error.value = null
      onConnected?.()
    },
    onDisconnected: () => {
      connected.value = false
      connecting.value = !manuallyClosed
      error.value = null
    },
    onError: (_ws, event) => {
      connected.value = false
      connecting.value = !manuallyClosed
      error.value = (event as Event)?.type ?? '连接错误'
    },
    onMessage: (_ws, event) => {
      try {
        onMessage(JSON.parse(event.data as string))
      } catch {
        // 忽略非法消息
      }
    },
  })

  function close() {
    manuallyClosed = true
    connected.value = false
    connecting.value = false
    ws.close()
  }

  return { connected, connecting, error, close }
}
