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

/** 更新用户（管理员：过期日期/状态/存储配额） */
export function updateUser(
  userId: string,
  data: {
    expires_at?: string | null
    status?: 'active' | 'disabled'
    /** 存储配额（字节），仅总管理员可设置，null 表示不限制 */
    storage_quota?: number | null
  }
) {
  return request.patch<any, UserInfo>(`/v1/admin/users/${userId}`, data)
}

/** 删除用户 */
export function deleteUser(userId: string) {
  return request.delete<any, { deleted: boolean }>(`/v1/admin/users/${userId}`)
}

/** 磁盘信息（存储目录所在物理磁盘） */
export interface DiskInfo {
  /** 配置的存储根目录 */
  path: string
  /** 实际探测到的挂载目录 */
  mount: string
  /** 总容量（字节） */
  total: number
  /** 已用（字节） */
  used: number
  /** 可用（字节） */
  free: number
  /** 已用百分比 */
  percent: number
}

/** 磁盘总览汇总 */
export interface DiskSummary {
  /** 租户数（含平台方） */
  tenant_count: number
  /** 不限制配额的账号数 */
  unlimited_count: number
  /** 有限额租户的配额总量（字节） */
  total_quota: number
  /** 所有租户实际用量（字节） */
  total_used: number
  /** 文档总数 */
  total_documents: number
  /** 配额整体使用率（%） */
  quota_used_percent: number
  /** 可分配配额上限 = 磁盘总容量 × 可分配比例（字节） */
  allocatable_capacity: number
  /** 可分配比例（如 0.7 表示 70%） */
  allocatable_ratio: number
  /** 剩余可分配配额 = 可分配上限 - 已分配配额（字节） */
  allocatable_remaining: number
  /** 是否超售：已分配配额超过可分配上限 */
  overcommit: boolean
}

/** 单个租户（账号）用量 */
export interface TenantUsage {
  id: string
  email: string
  nickname: string | null
  role: 'super_admin' | 'tenant_admin'
  status: string
  quota: number | null
  unlimited: boolean
  used: number
  percent: number | null
  user_count: number
  document_count: number
  created_at: string | null
  expires_at: string | null
}

/** 磁盘管理总览 */
export interface DiskOverview {
  disk: DiskInfo
  summary: DiskSummary
  tenants: TenantUsage[]
}

/** 磁盘管理总览（总容量与各租户用量） */
export function getDiskOverview() {
  return request.get<any, DiskOverview>('/v1/admin/disk')
}
