import request from '@/utils/request'

// Collection API

export interface Collection {
  id: string
  name: string
  description: string
  embedding_model: string
  vector_dim: number
  distance_metric: string
  is_active: boolean
  document_count: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface CollectionCreate {
  name: string
  description?: string
  embedding_model?: string
  vector_dim?: number
  distance_metric?: string
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export function getCollections(params: { page?: number; page_size?: number; keyword?: string }) {
  return request.get<any, Paginated<Collection>>('/v1/collections', { params })
}

export function createCollection(data: CollectionCreate) {
  return request.post<any, Collection>('/v1/collections', data)
}

export function getCollection(id: string) {
  return request.get<any, Collection>(`/v1/collections/${id}`)
}

export function updateCollection(id: string, data: any) {
  return request.put<any, Collection>(`/v1/collections/${id}`, data)
}

export function deleteCollection(id: string) {
  return request.delete<any, void>(`/v1/collections/${id}`)
}

export function getCollectionStats(id: string) {
  return request.get<any, any>(`/v1/collections/${id}/stats`)
}
