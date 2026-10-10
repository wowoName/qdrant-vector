/**
 * 前端时间工具：统一按北京时间（Asia/Shanghai）解析并展示后端时间戳。
 *
 * 后端所有时间列均为 TIMESTAMP WITHOUT TIME ZONE，并以北京时间（app_timezone）
 * 生成，序列化后形如 "2026-10-11T00:15:39"（无时区后缀）。
 * 浏览器 new Date()/dayjs() 默认按客户端本地时区解析无后缀字符串，会导致非 +8
 * 时区客户端出现偏移。此工具统一按北京时间解析，并统一渲染为北京时间墙钟，
 * 保证跨时区展示一致。
 */
import dayjs, { type Dayjs } from 'dayjs'
import timezone from 'dayjs/plugin/timezone'
import utc from 'dayjs/plugin/utc'

dayjs.extend(utc)
dayjs.extend(timezone)

/** 应用时区（与后端 app_timezone 保持一致） */
export const APP_TIMEZONE = 'Asia/Shanghai'

// 形如 2026-10-11T00:15:39 / 2026-10-11 00:15:39（可带毫秒），且无时区后缀
const NAIVE_DATETIME_RE = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?$/

/**
 * 解析后端时间值为 dayjs 对象。
 * 无时区后缀的日期时间串按北京时间解释；已带时区（Z / ±hh:mm）按原样解析。
 * 空值返回 null。
 */
export function parseAppTime(value?: string | number | Date | null): Dayjs | null {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (NAIVE_DATETIME_RE.test(trimmed)) {
      // 无时区后缀 → 视为北京时间，得到正确的瞬时
      return dayjs.tz(trimmed.replace(' ', 'T'), APP_TIMEZONE)
    }
    return dayjs(trimmed)
  }
  return dayjs(value)
}

/**
 * 解析并按北京时间格式化（跨时区一致）。
 * 空值返回空串。
 */
export function formatAppTime(
  value?: string | number | Date | null,
  template = 'YYYY-MM-DD HH:mm:ss',
): string {
  const d = parseAppTime(value)
  return d ? d.tz(APP_TIMEZONE).format(template) : ''
}

/** 当前北京时间 */
export function nowApp(): Dayjs {
  return dayjs().tz(APP_TIMEZONE)
}
