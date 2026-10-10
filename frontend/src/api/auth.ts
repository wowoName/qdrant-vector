import request from '@/utils/request'
import type { UserInfo } from '@/utils/user'

// Auth API

export interface LoginResponse {
  token: string
  user: UserInfo
  /** 注册时若系统可分配磁盘空间不足，返回提示文案（提示用户联系管理员分配） */
  quota_warning?: string
}

export interface SendCodeResponse {
  sent: boolean
  smtp_configured: boolean
  expire_minutes: number
  dev_code: string | null
  message: string
}

/** 发送注册验证码 */
export function sendCode(email: string) {
  return request.post<any, SendCodeResponse>('/v1/auth/send-code', { email })
}

/** 注册（邮箱 + 验证码 + 密码） */
export function register(data: {
  email: string
  code: string
  password: string
  nickname?: string
}) {
  return request.post<any, LoginResponse>('/v1/auth/register', data)
}

/** 登录 */
export function login(data: { email: string; password: string }) {
  return request.post<any, LoginResponse>('/v1/auth/login', data)
}

/** 获取当前用户信息 */
export function getMe() {
  return request.get<any, UserInfo>('/v1/auth/me')
}

/** 修改当前用户信息（昵称/密码，不含过期日期）；改密码需传 old_password 二次校验 */
export function updateMe(data: { nickname?: string; old_password?: string; password?: string }) {
  return request.patch<any, UserInfo>('/v1/auth/me', data)
}
