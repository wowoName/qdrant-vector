import request from '@/utils/request'
import type { UserInfo } from '@/utils/user'

// Admin API - 用户管理（管理员）

/** 用户树（租户 → 子用户） */
export interface UserTreeNode extends UserInfo {
  children?: UserTreeNode[]
}

/** 用户树查询条件 */
export interface UserTreeParams {
  keyword?: string
  tenant?: string
  status?: string
  created_from?: string
  created_to?: string
  expires_from?: string
  expires_to?: string
}

/** 用户树（管理员为根节点，children 为其名下子用户） */
export function getUserTree(params: UserTreeParams) {
  return request.get<any, UserTreeNode[]>('/v1/admin/user-tree', { params })
}

/** 创建用户（管理员开通） */
export function createUser(data: {
  email: string
  password: string
  nickname?: string
  expires_at?: string | null
}) {
  return request.post<any, UserInfo>('/v1/admin/users', data)
}

/** 更新用户（管理员：仅过期日期/状态） */
export function updateUser(
  userId: string,
  data: {
    expires_at?: string | null
    status?: 'active' | 'disabled'
  }
) {
  return request.patch<any, UserInfo>(`/v1/admin/users/${userId}`, data)
}

/** 删除用户 */
export function deleteUser(userId: string) {
  return request.delete<any, { deleted: boolean }>(`/v1/admin/users/${userId}`)
}
