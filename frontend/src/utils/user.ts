/**
 * 用户认证状态管理 - JWT Token
 *
 * 基于 localStorage 存储 JWT token 和当前用户信息。
 */

const TOKEN_KEY = 'vdm_token'
const USER_KEY = 'vdm_user'

export interface UserInfo {
  id: string
  email: string
  nickname: string | null
  role: 'super_admin' | 'tenant_admin' | 'user'
  owner_admin_id: string | null
  status: string
  is_verified: boolean
  created_at: string | null
  expires_at: string | null
  last_login_at: string | null
  /** 所属租户存储配额（字节），null 表示不限制 */
  storage_quota: number | null
  /** 所属租户已用空间（字节） */
  storage_used?: number
  /** 是否不限制存储空间 */
  storage_unlimited?: boolean
}

/** 获取 token */
export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

/** 获取当前用户信息 */
export function getUser(): UserInfo | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as UserInfo
  } catch {
    return null
  }
}

/** 是否已登录 */
export function isLoggedIn(): boolean {
  return !!getToken()
}

/** 是否管理员（总管理员或租户管理员） */
export function isAdmin(): boolean {
  const role = getUser()?.role
  return role === 'super_admin' || role === 'tenant_admin'
}

/** 是否总管理员 */
export function isSuperAdmin(): boolean {
  return getUser()?.role === 'super_admin'
}

/** 是否租户管理员 */
export function isTenantAdmin(): boolean {
  return getUser()?.role === 'tenant_admin'
}

/** 角色中文标签 */
export function roleLabel(): string {
  const role = getUser()?.role
  if (role === 'super_admin') return '总管理员'
  if (role === 'tenant_admin') return '租户管理员'
  return '普通用户'
}

/** 是否有写权限（自用号 role=user 对知识库/文档只读） */
export function canWrite(): boolean {
  return getUser()?.role !== 'user'
}

/** 保存登录态 */
export function setAuth(token: string, user: UserInfo): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

/** 仅刷新用户信息 */
export function setUser(user: UserInfo): void {
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

/** 清除登录态（退出登录） */
export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

/** 获取 Authorization 请求头值 */
export function authHeader(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export default {
  getToken,
  getUser,
  isLoggedIn,
  isAdmin,
  isSuperAdmin,
  isTenantAdmin,
  roleLabel,
  canWrite,
  setAuth,
  setUser,
  clearAuth,
  authHeader,
}
