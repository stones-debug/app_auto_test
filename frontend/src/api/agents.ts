import request from '@/utils/request'

import type { PageData } from './projects'

export interface Agent {
  id: number
  agent_id: string
  hostname: string | null
  platform: string | null
  ip: string | null
  status: string
  version: string | null
  last_heartbeat: string | null
  created_at: string
  device_count: number
}

export interface CreateAgentResult {
  id: number
  agent_id: string
  agent_key: string
  hostname: string | null
  platform: string | null
  status: string
}

export interface Device {
  id: number
  agent_id: number
  agent_name: string | null
  name: string
  platform: string
  platform_version: string | null
  udid: string
  device_type: string
  status: string
  connection_type: string
  address: string | null
  locked_by_execution: number | null
  last_heartbeat: string | null
}

export interface DefaultDevice {
  device_id: number | null
  device: Device | null
  available: boolean
  reason: string
}

export interface AgentBinding {
  id: number
  agent_id: number
  user_id: number
  username: string
}

export function listAgents() {
  return request.get<Agent[]>('/agents')
}

export function createAgent(data: { hostname?: string; platform?: string }) {
  return request.post<CreateAgentResult>('/agents', data)
}

export function deleteAgent(id: number) {
  return request.delete<void>(`/agents/${id}`)
}

export function agentDevices(agentId: number) {
  return request.get<Device[]>(`/agents/${agentId}/devices`)
}

export function listDevices(params?: { platform?: string; status?: string; page?: number; page_size?: number }) {
  return request.get<PageData<Device>>('/devices', { params })
}

export function getDevice(id: number) {
  return request.get<Device>(`/devices/${id}`)
}

export function releaseDevice(id: number) {
  return request.post<Device>(`/devices/${id}/release`)
}

export function getDefaultDevice() {
  return request.get<DefaultDevice>('/devices/default')
}

export function setDefaultDevice(deviceId: number | null) {
  return request.put<DefaultDevice>('/devices/default', { device_id: deviceId })
}

export function unbindMyAgent(agentId: number) {
  return request.delete<void>(`/agents/${agentId}/bindings/me`)
}

export function deviceStatusType(s: string): 'success' | 'info' | 'warning' | 'danger' {
  if (s === 'idle') return 'success'
  if (s === 'busy') return 'warning'
  if (s === 'online') return 'success'
  if (s === 'offline') return 'info'
  return 'danger'
}

export function agentStatusType(s: string): 'success' | 'info' | 'warning' | 'danger' {
  if (s === 'online') return 'success'
  if (s === 'busy') return 'warning'
  if (s === 'offline') return 'info'
  return 'danger'
}
