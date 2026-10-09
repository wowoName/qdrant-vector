import axios from 'axios'
import { ElMessage } from 'element-plus'
import { authHeader, clearAuth } from './user'
import router from '@/router'

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 60000,
})

// 请求拦截器：携带 JWT
request.interceptors.request.use(
  (config) => {
    Object.assign(config.headers, authHeader())
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    const data = response.data
    if (data && data.code === 0) {
      return data.data
    }
    ElMessage.error(data.message || '请求失败')
    return Promise.reject(data)
  },
  (error) => {
    const status = error.response?.status
    const message =
      error.response?.data?.message || error.response?.data?.detail || error.message || '网络错误'

    // 未登录 / token 失效：清除登录态并跳转登录页
    if (status === 401) {
      clearAuth()
      if (router.currentRoute.value.path !== '/login') {
        ElMessage.error('登录已过期，请重新登录')
        router.push('/login')
      }
      return Promise.reject(error)
    }

    ElMessage.error(typeof message === 'string' ? message : '请求失败')
    return Promise.reject(error)
  }
)

export default request
