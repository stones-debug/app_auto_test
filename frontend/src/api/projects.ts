import request from '@/utils/request'

export interface Project {
  id: number
  name: string
  description?: string | null
  owner_id: number
  visibility: string
  status: string
  role?: string | null
  case_count?: number
  element_count?: number
  suite_count?: number
  created_at: string
  updated_at: string
}

export interface ProjectMember {
  id: number
  project_id: number
  user_id: number
  role: string
  username?: string | null
}

export interface PageData<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export function listProjects(params?: { page?: number; page_size?: number; visibility?: string }) {
  return request.get<PageData<Project>>('/projects', { params })
}

export function createProject(data: { name: string; description?: string; visibility?: string }) {
  return request.post<Project>('/projects', data)
}

export function getProject(id: number) {
  return request.get<Project>(`/projects/${id}`)
}

export function updateProject(id: number, data: Partial<Project>) {
  return request.put<Project>(`/projects/${id}`, data)
}

export function deleteProject(id: number) {
  return request.delete<void>(`/projects/${id}`)
}

export function listMembers(projectId: number) {
  return request.get<ProjectMember[]>(`/projects/${projectId}/members`)
}

export function addMember(projectId: number, data: { user_id: number; role: string }) {
  return request.post<ProjectMember>(`/projects/${projectId}/members`, data)
}