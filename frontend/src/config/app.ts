/**
 * 应用级配置
 *
 * 通过环境变量注入，便于统一管理与替换。
 */

/** 应用显示名称（浏览器标题、侧边栏品牌、登录页等） */
export const APP_NAME: string = import.meta.env.VITE_APP_NAME || '新页'

/** 技术支持联系方式（QQ 号），可在环境变量 VITE_CONTACT_QQ 中覆盖 */
export const CONTACT_QQ: string = import.meta.env.VITE_CONTACT_QQ || '1069722589'
