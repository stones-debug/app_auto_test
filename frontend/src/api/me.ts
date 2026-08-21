import request from '@/utils/request'

export interface AgentKey {
  exists: boolean
  public_id: string | null
  key: string | null
}

export interface AgentKeyCreated {
  exists: boolean
  public_id: string
  key: string
}

export function getMyAgentKey() {
  return request.get<AgentKey>('/me/agent-key')
}

export function createMyAgentKey() {
  return request.post<AgentKeyCreated>('/me/agent-key')
}

export function regenerateMyAgentKey() {
  return request.post<AgentKeyCreated>('/me/agent-key/regenerate')
}
