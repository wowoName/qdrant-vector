/**
 * 用户标识管理 - 匿名用户 ID
 *
 * 基于 localStorage 存储匿名用户 ID，用于区分不同用户的会话。
 * 不依赖后端用户系统，纯前端生成。
 */

const USER_ID_KEY = 'vdm_user_id'

/**
 * 获取当前用户 ID（如果没有则生成一个）
 */
export function getUserId(): string {
  let userId = localStorage.getItem(USER_ID_KEY)
  if (!userId) {
    userId = generateUserId()
    localStorage.setItem(USER_ID_KEY, userId)
  }
  return userId
}

/**
 * 生成用户 ID
 * 格式：anon_ + 随机字符串（16 位十六进制）
 */
function generateUserId(): string {
  const chars = 'abcdef0123456789'
  let result = 'anon_'
  for (let i = 0; i < 16; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length))
  }
  return result
}

/**
 * 重置用户 ID（用于测试或清除）
 */
export function resetUserId(): string {
  const newId = generateUserId()
  localStorage.setItem(USER_ID_KEY, newId)
  return newId
}

export default { getUserId, resetUserId }
