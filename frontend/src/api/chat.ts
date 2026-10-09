import request from '@/utils/request'
import { authHeader } from '@/utils/user'

// Chat API

export interface ChatReference {
  chunk_id: string
  document_id: string
  document_name: string
  chunk_index: number
  text: string
  score: number
  page_num?: number
  collection_id?: string
}

export interface ChatResponse {
  session_id: string
  answer: string
  reasoning: string
  references: ChatReference[]
  latency_ms: number
  model: string
  is_kb_answer: boolean
}

export interface ChatRequest {
  query: string
  collection_id?: string
  session_id?: string
  stream?: boolean
  top_k?: number
  temperature?: number
  max_tokens?: number
  use_rewrite?: boolean
}

export interface Conversation {
  session_id: string
  collection_id: string | null
  title: string | null
  message_count: number
  total_tokens: number
  status: string
  created_at: string
  updated_at: string
}

export interface ConversationListResponse {
  items: Conversation[]
  total: number
  page: number
  page_size: number
}

export interface ConversationDetail extends Conversation {
  messages: { role: string; content: string }[]
}

/**
 * 非流式对话（全局接口，collection_id 可选）
 */
export function chatAskGlobal(data: ChatRequest) {
  return request.post<any, ChatResponse>('/v1/chat', data)
}

/**
 * 流式对话（全局接口，collection_id 可选，基于 fetch + ReadableStream）
 */
export async function chatStreamGlobal(
  data: ChatRequest,
  onMessage?: (event: any) => void,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const baseURL = import.meta.env.VITE_API_BASE_URL || '/api'
  const url = `${baseURL}/v1/chat/stream`

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
    },
    body: JSON.stringify(data),
    signal,
  })

  if (!response.ok) {
    const errText = await response.text()
    throw new Error(`请求失败: ${response.status} ${errText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('浏览器不支持流式读取')
  }

  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalAnswer = ''
  let finalReasoning = ''
  let sessionId = ''
  let references: ChatReference[] = []
  let latencyMs = 0
  let model = ''
  let isKbAnswer = true

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // 按 "data: " 前缀分割事件
      const lines = buffer.split('\n\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        if (!trimmed.startsWith('data: ')) continue

        const jsonStr = trimmed.slice(6) // 去掉 "data: "
        if (!jsonStr) continue

        try {
          const event = JSON.parse(jsonStr)
          if (event.type === 'metadata') {
            sessionId = event.session_id
            references = event.references || []
            isKbAnswer = event.is_kb_answer ?? true
          } else if (event.type === 'reasoning') {
            finalReasoning += event.content
          } else if (event.type === 'content') {
            finalAnswer += event.content
          } else if (event.type === 'done') {
            finalAnswer = event.answer || finalAnswer
            finalReasoning = event.reasoning || finalReasoning
            latencyMs = event.latency_ms || 0
            model = event.model || ''
            isKbAnswer = event.is_kb_answer ?? isKbAnswer
          }
          if (onMessage) {
            onMessage(event)
          }
        } catch (e) {
          console.warn('SSE 事件解析失败:', jsonStr, e)
        }
      }
    }
  } finally {
    reader.releaseLock()
  }

  return {
    session_id: sessionId,
    answer: finalAnswer,
    reasoning: finalReasoning,
    references,
    latency_ms: latencyMs,
    model,
    is_kb_answer: isKbAnswer,
  }
}

/**
 * 全局会话列表（最近对话）
 */
export function listConversationsGlobal(page = 1, page_size = 20) {
  return request.get<any, ConversationListResponse>('/v1/chat/conversations', {
    params: { page, page_size },
  })
}

// ========== 兼容旧版 API（按知识库分组） ==========

/**
 * 非流式对话
 */
export function chatAsk(collectionId: string, data: ChatRequest) {
  return request.post<any, ChatResponse>(`/v1/collections/${collectionId}/chat`, data)
}

/**
 * 流式对话（基于 fetch + ReadableStream 解析 SSE）
 *
 * @param collectionId 知识库 ID
 * @param data 请求参数
 * @param onMessage 每收到一条完整事件的回调
 * @param signal 用于取消请求的 AbortSignal
 * @returns Promise<ChatResponse> 最终完整响应
 */
export async function chatStream(
  collectionId: string,
  data: ChatRequest,
  onMessage?: (event: any) => void,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const baseURL = import.meta.env.VITE_API_BASE_URL || '/api'
  const url = `${baseURL}/v1/collections/${collectionId}/chat/stream`

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
    },
    body: JSON.stringify(data),
    signal,
  })

  if (!response.ok) {
    const errText = await response.text()
    throw new Error(`请求失败: ${response.status} ${errText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('浏览器不支持流式读取')
  }

  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalAnswer = ''
  let finalReasoning = ''
  let sessionId = ''
  let references: ChatReference[] = []
  let latencyMs = 0
  let model = ''
  let isKbAnswer = true

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // 按 "data: " 前缀分割事件
      const lines = buffer.split('\n\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        if (!trimmed.startsWith('data: ')) continue

        const jsonStr = trimmed.slice(6) // 去掉 "data: "
        if (!jsonStr) continue

        try {
          const event = JSON.parse(jsonStr)
          if (event.type === 'metadata') {
            sessionId = event.session_id
            references = event.references || []
            isKbAnswer = event.is_kb_answer ?? true
          } else if (event.type === 'reasoning') {
            finalReasoning += event.content
          } else if (event.type === 'content') {
            finalAnswer += event.content
          } else if (event.type === 'done') {
            finalAnswer = event.answer || finalAnswer
            finalReasoning = event.reasoning || finalReasoning
            latencyMs = event.latency_ms || 0
            model = event.model || ''
            isKbAnswer = event.is_kb_answer ?? isKbAnswer
          }
          if (onMessage) {
            onMessage(event)
          }
        } catch (e) {
          // 忽略解析失败的事件
          console.warn('SSE 事件解析失败:', jsonStr, e)
        }
      }
    }
  } finally {
    reader.releaseLock()
  }

  return {
    session_id: sessionId,
    answer: finalAnswer,
    reasoning: finalReasoning,
    references,
    latency_ms: latencyMs,
    model,
    is_kb_answer: isKbAnswer,
  }
}

/**
 * 获取会话详情
 */
export function getConversation(sessionId: string) {
  return request.get<any, ConversationDetail>(`/v1/chat/sessions/${sessionId}`)
}

/**
 * 删除会话
 */
export function deleteConversation(sessionId: string) {
  return request.delete<any, { deleted: boolean }>(`/v1/chat/sessions/${sessionId}`)
}

/**
 * 清除会话历史（兼容旧版）
 */
export function clearChatSession(sessionId: string) {
  return request.delete<any, { cleared: boolean }>(`/v1/chat/sessions/${sessionId}/clear`)
}
