import request from '@/utils/request'

export interface ReleaseManifest {
  version: string
  filename: string
  sha256: string
  size: number
  published_at: string
}

export interface DownloadToken {
  token: string
  filename: string
}

export function getLatestRelease() {
  return request.get<ReleaseManifest>('/agent/releases/latest')
}

export function getDownloadToken() {
  return request.post<DownloadToken>('/agent/releases/latest/download-token')
}

export function releaseDownloadUrl(filename: string, token: string): string {
  return `/api/agent/releases/download/${encodeURIComponent(filename)}?token=${encodeURIComponent(token)}`
}
