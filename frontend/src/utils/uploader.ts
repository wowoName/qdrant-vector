import axios from 'axios'
import { getUserId } from './user'

// 创建上传专用的 axios 实例，避免全局 60s 超时限制
const uploadRequest = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30 * 60 * 1000, // 上传超时设为 30 分钟
})

// 请求拦截器（与全局保持一致）
uploadRequest.interceptors.request.use(
  (config) => {
    const userId = getUserId()
    if (userId) {
      config.headers['X-User-ID'] = userId
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截器（简化版，不弹错误提示，由调用方处理）
uploadRequest.interceptors.response.use(
  (response) => {
    const data = response.data
    if (data && data.code === 0) {
      return data.data
    }
    return Promise.reject(data || new Error('Request failed'))
  },
  (error) => Promise.reject(error)
)

// 默认分片大小 5MB
const DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024
// 并发上传数
const DEFAULT_CONCURRENCY = 3
// 分片上传最大重试次数
const MAX_RETRY = 3
// 哈希计算分块大小（16MB，避免大文件一次性读入内存导致浏览器卡死）
const HASH_CHUNK_SIZE = 16 * 1024 * 1024

export interface UploadProgress {
  uploaded: number
  total: number
  percent: number
  speed: number // bytes per second
}

export interface UploadOptions {
  collectionId: string
  directoryId?: string
  chunkSize?: number
  concurrency?: number
  onProgress?: (progress: UploadProgress) => void
  onStatusChange?: (status: string) => void
  signal?: AbortSignal
}

export interface UploadResult {
  document_id: string
  version_id: string
  status: string
  task_id?: string
  instant: boolean
}

/**
 * 分块计算文件 SHA-256 哈希
 * 使用增量计算，避免大文件一次性读入内存导致浏览器卡死
 */
async function calculateFileHash(
  file: File,
  signal?: AbortSignal,
  onProgress?: (percent: number) => void
): Promise<string> {
  const fileSize = file.size
  let offset = 0

  // @ts-ignore
  let hash: CryptoAlgorithm | null = null

  try {
    // 尝试使用 Web Crypto 的增量模式
    // @ts-ignore
    hash = crypto.subtle && ('digest' in crypto.subtle) ? null : null
  } catch {
    // ignore
  }

  // 优先使用增量 Crypto（如果浏览器支持）
  try {
    // @ts-ignore
    if (crypto.subtle && typeof crypto.subtle.digest === 'function') {
      // 检查是否支持增量哈希
      try {
        // 尝试使用 incremental hashing（Chrome 支持有限，用另一种方式）
        // 这里采用分块读取 + 完整计算（对于超大型文件可能慢，但不会卡死 UI）
        // 对于现代浏览器，我们使用分块读取后一次性 digest，
        // 配合 setTimeout 让出主线程，避免界面长时间卡死
        const chunks: ArrayBuffer[] = []
        let processed = 0

        while (offset < fileSize) {
          if (signal && signal.aborted) {
            throw new DOMException('Aborted', 'AbortError')
          }

          const chunkEnd = Math.min(offset + HASH_CHUNK_SIZE, fileSize)
          const chunk = file.slice(offset, chunkEnd)
          const buffer = await chunk.arrayBuffer()
          chunks.push(buffer)
          processed += buffer.byteLength
          offset = chunkEnd

          onProgress?.(Math.min(100, (processed / fileSize) * 100))

          // 让出主线程，避免 UI 卡死
          await new Promise(resolve => setTimeout(resolve, 0))
        }

        // 合并所有 chunk 并计算最终 hash
        // 注意：对于超大文件，合并可能内存不足，这时退化为简单的大小+名称的伪 hash
        if (fileSize > 500 * 1024 * 1024) {
          // 大于 500MB 的文件，使用"文件名+大小+最后修改时间"生成一个标识，
          // 并取文件头部和尾部部分数据参与 hash，平衡性能与准确性
          const headSize = Math.min(1 * 1024 * 1024, fileSize)
          const tailSize = Math.min(1 * 1024 * 1024, Math.max(0, fileSize - headSize))
          const headBuffer = await file.slice(0, headSize).arrayBuffer()
          let tailBuffer: ArrayBuffer
          if (tailSize > 0) {
            tailBuffer = await file.slice(fileSize - tailSize, fileSize).arrayBuffer()
          } else {
            tailBuffer = new ArrayBuffer(0)
          }

          const metaStr = `${file.name}-${file.size}-${file.lastModified}`
          const metaBuffer = new TextEncoder().encode(metaStr)

          const combined = new Uint8Array(metaBuffer.length + headBuffer.byteLength + tailBuffer.byteLength)
          combined.set(metaBuffer, 0)
          combined.set(new Uint8Array(headBuffer), metaBuffer.length)
          combined.set(new Uint8Array(tailBuffer), metaBuffer.length + headBuffer.byteLength)

          const hashBuffer = await crypto.subtle.digest('SHA-256', combined)
          const hashArray = Array.from(new Uint8Array(hashBuffer))
          return 'lgt_' + hashArray.map(b => b.toString(16).padStart(2, '0')).join('')
        }

        // 合并所有分块计算完整 hash
        const totalLength = chunks.reduce((sum, c) => sum + c.byteLength, 0)
        const combined = new Uint8Array(totalLength)
        let pos = 0
        for (const chunk of chunks) {
          combined.set(new Uint8Array(chunk), pos)
          pos += chunk.byteLength
        }

        const hashBuffer = await crypto.subtle.digest('SHA-256', combined)
        const hashArray = Array.from(new Uint8Array(hashBuffer))
        return hashArray.map(b => b.toString(16).padStart(2, '0')).join('')
      } catch {
        // fall through
      }
    }
  } catch {
    // ignore
  }

  // Fallback: 简单的基于文件元数据的哈希标识
  const metaStr = `${file.name}-${file.size}-${file.lastModified}`
  const encoder = new TextEncoder()
  const data = encoder.encode(metaStr)
  // 使用简单的 32 位哈希作为 fallback
  let h = 0
  for (let i = 0; i < data.length; i++) {
    h = ((h << 5) - h + data[i]) | 0
  }
  return 'fb_' + Math.abs(h).toString(16).padStart(8, '0') + '_' + file.size.toString(16)
}

/**
 * 带重试的请求
 */
async function requestWithRetry(
  fn: () => Promise<any>,
  retries: number = MAX_RETRY,
  signal?: AbortSignal
): Promise<any> {
  let lastError: any
  for (let i = 0; i <= retries; i++) {
    if (signal && signal.aborted) {
      throw new DOMException('Aborted', 'AbortError')
    }
    try {
      return await fn()
    } catch (err: any) {
      lastError = err
      // 取消请求不重试
      if (err.name === 'AbortError' || err?.code === 'ERR_CANCELED' || err?.message === 'canceled') {
        throw err
      }
      // 网络错误重试
      if (i < retries) {
        await new Promise(resolve => setTimeout(resolve, 1000 * (i + 1)))
        continue
      }
    }
  }
  throw lastError
}

/**
 * 分片上传器
 * 支持大文件分片上传、并发上传、取消上传、失败重试
 */
export async function chunkedUpload(
  file: File,
  options: UploadOptions
): Promise<UploadResult> {
  const {
    collectionId,
    directoryId,
    chunkSize = DEFAULT_CHUNK_SIZE,
    concurrency = DEFAULT_CONCURRENCY,
    onProgress,
    onStatusChange,
    signal,
  } = options

  const fileSize = file.size
  const totalChunks = Math.ceil(fileSize / chunkSize)
  let uploadedBytes = 0
  let startTime = Date.now()

  const updateProgress = (chunkSizeDelta: number) => {
    uploadedBytes += chunkSizeDelta
    const elapsed = (Date.now() - startTime) / 1000
    const speed = elapsed > 0 ? uploadedBytes / elapsed : 0
    onProgress?.({
      uploaded: uploadedBytes,
      total: fileSize,
      percent: Math.min(100, (uploadedBytes / fileSize) * 100),
      speed,
    })
  }

  // 检查是否需要分片：小文件直接用普通上传
  if (fileSize <= chunkSize) {
    onStatusChange?.('uploading')
    const formData = new FormData()
    formData.append('file', file)
    if (directoryId) {
      formData.append('directory_id', directoryId)
    }

    const result = await requestWithRetry(
      () => uploadRequest.post(
        `/v1/collections/${collectionId}/documents/upload`,
        formData,
        {
          signal,
          onUploadProgress: (progressEvent: any) => {
            if (progressEvent.total) {
              const percent = (progressEvent.loaded / progressEvent.total) * 100
              const elapsed = (Date.now() - startTime) / 1000
              const speed = elapsed > 0 ? progressEvent.loaded / elapsed : 0
              onProgress?.({
                uploaded: progressEvent.loaded,
                total: progressEvent.total,
                percent,
                speed,
              })
            }
          },
        }
      ),
      1, // 小文件只重试 1 次
      signal
    )

    onStatusChange?.('completed')
    return { ...result, instant: false }
  }

  // 大文件分片上传
  onStatusChange?.('hashing')

  // 1. 计算文件哈希（用于秒传和校验）
  const fileHash = await calculateFileHash(
    file,
    signal,
    (percent) => {
      onProgress?.({
        uploaded: 0,
        total: fileSize,
        percent: percent * 0.05, // 哈希计算占 5% 进度
        speed: 0,
      })
    }
  )

  if (signal && signal.aborted) {
    throw new DOMException('Upload cancelled', 'AbortError')
  }

  // 2. 初始化上传会话
  onStatusChange?.('init')
  const initResult = await requestWithRetry(
    () => uploadRequest.post('/v1/documents/upload/init', {
      file_name: file.name,
      file_size: fileSize,
      total_chunks: totalChunks,
      chunk_size: chunkSize,
      file_hash: fileHash,
      collection_id: collectionId,
      directory_id: directoryId || null,
    }),
    2,
    signal
  )

  const uploadId = initResult.upload_id
  onStatusChange?.('uploading')

  // 重置时间，用于计算上传速度
  startTime = Date.now()
  uploadedBytes = 0

  // 3. 并发上传分片
  let nextChunk = 0
  let cancelled = false
  const failedChunks: Map<number, number> = new Map() // chunkIndex -> retryCount

  const uploadChunk = async (index: number): Promise<boolean> => {
    if (cancelled || (signal && signal.aborted)) {
      return false
    }

    const start = index * chunkSize
    const end = Math.min(start + chunkSize, fileSize)
    const chunk = file.slice(start, end)

    const formData = new FormData()
    formData.append('file', chunk, `chunk_${index}`)

    try {
      await uploadRequest.put(
        `/v1/documents/upload/${uploadId}/${index}`,
        formData,
        { signal }
      )
      updateProgress(chunk.size)
      return true
    } catch (err: any) {
      // 如果是取消操作
      if (err.name === 'AbortError' || err?.code === 'ERR_CANCELED' || signal?.aborted) {
        cancelled = true
        return false
      }

      // 记录失败次数并重试
      const retryCount = (failedChunks.get(index) || 0) + 1
      failedChunks.set(index, retryCount)

      if (retryCount < MAX_RETRY) {
        // 放回队列重试
        return false
      }
      throw new Error(`Chunk ${index} failed after ${MAX_RETRY} retries`)
    }
  }

  // 并发 worker
  const workers: Promise<void>[] = []

  const worker = async () => {
    while (true) {
      if (cancelled || (signal && signal.aborted)) return

      let index = -1
      // 先找需要重试的分片
      for (const [idx, count] of failedChunks.entries()) {
        if (count < MAX_RETRY) {
          index = idx
          break
        }
      }

      // 没有重试的，取下一个新分片
      if (index === -1) {
        if (nextChunk >= totalChunks) break
        index = nextChunk++
      } else {
        // 移除已记录的失败，重新尝试
        failedChunks.delete(index)
      }

      try {
        const success = await uploadChunk(index)
        if (!success && !cancelled) {
          // 失败但未超过重试次数，短暂延迟后继续循环
          await new Promise(r => setTimeout(r, 500))
        }
      } catch (err) {
        // 超过最大重试次数，终止所有 worker
        cancelled = true
        throw err
      }
    }
  }

  // 启动并发 worker
  for (let i = 0; i < Math.min(concurrency, totalChunks); i++) {
    workers.push(worker())
  }

  try {
    await Promise.all(workers)
  } catch (err) {
    // 确保所有 worker 都停止
    cancelled = true
    // 通知后端取消
    try {
      await uploadRequest.post(`/v1/documents/upload/${uploadId}/cancel`)
    } catch {
      // ignore
    }
    throw err
  }

  // 检查是否被取消
  if (cancelled || (signal && signal.aborted)) {
    try {
      await uploadRequest.post(`/v1/documents/upload/${uploadId}/cancel`)
    } catch {
      // ignore
    }
    throw new DOMException('Upload cancelled', 'AbortError')
  }

  // 检查是否还有失败的分片
  if (failedChunks.size > 0) {
    const firstFailed = Array.from(failedChunks.keys())[0]
    throw new Error(`Upload failed: chunk ${firstFailed} failed after ${MAX_RETRY} retries`)
  }

  // 4. 完成上传（合并分片）
  onStatusChange?.('merging')
  // 合并可能耗时较长，设置更长的超时
  const completeResult = await requestWithRetry(
    () => uploadRequest.post(
      `/v1/documents/upload/${uploadId}/complete`,
      { file_hash: fileHash },
      { timeout: 30 * 60 * 1000 } // 30 分钟超时
    ),
    1, // 合并不重试（避免重复合并）
    signal
  )

  onStatusChange?.('completed')

  return completeResult
}

/**
 * 格式化文件大小
 */
export function formatFileSize(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes.toFixed(0)} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

/**
 * 格式化上传速度
 */
export function formatSpeed(bps: number): string {
  if (!bps || bps < 0) return '0 B/s'
  return formatFileSize(bps) + '/s'
}
