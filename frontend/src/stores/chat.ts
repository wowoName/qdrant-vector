import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  chatStreamGlobal,
  getConversation,
  listConversationsGlobal,
  type ChatReference,
  type Conversation,
} from '@/api/chat'
import { getCollections, type Collection } from '@/api/collection'

/** 按文档分组后的引用，用于前端展示 */
export interface GroupedReference {
  document_id: string
  document_name: string
  collection_id?: string
  collection_name?: string
  chunks: ChatReference[]
  expanded: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
  loading?: boolean
  references?: ChatReference[]
  showRefs?: boolean
  showReasoning?: boolean
  latency?: number
  model?: string
  isKbAnswer?: boolean
  isCommand?: boolean
  groupedRefs?: GroupedReference[]
}

/** 从答案末尾剥离非知识库警示语（用于独立显示警示条） */
function stripNonKbWarning(answer: string): string {
  if (!answer) return ''
  const patterns = [
    /\n*\s*>?\s*⚠️\s*\*?\*?提示\*?\*?[：:].*?不保证准确性。?\s*$/gs,
    /\n*\s*⚠️\s*\*?\*?提示\*?\*?[：:].*?不保证准确性。?\s*$/gs,
  ]
  let result = answer
  for (const pattern of patterns) {
    result = result.replace(pattern, '').trimEnd()
  }
  return result
}

export const useChatStore = defineStore('chat', () => {
  // ---------------- 基础数据 ----------------
  const conversations = ref<Conversation[]>([])
  const collections = ref<Collection[]>([])
  const loadingConversations = ref(false)

  // ---------------- 对话状态 ----------------
  const messages = ref<ChatMessage[]>([])
  const sessionId = ref('')
  const collectionId = ref('')
  const isSending = ref(false)
  const streamMode = ref(true)

  // ---------------- 参数 ----------------
  const topK = ref(5)
  const temperature = ref(0.7)
  const maxTokens = ref(2048)
  const useRewrite = ref(true)

  let abortController: AbortController | null = null

  const hasMessages = computed(() => messages.value.length > 0)
  const currentCollection = computed(() =>
    collections.value.find((c) => c.id === collectionId.value),
  )

  function getCollectionName(cid?: string | null): string {
    if (!cid) return ''
    return collections.value.find((c) => c.id === cid)?.name || cid.slice(0, 8)
  }

  // ---------------- 数据拉取 ----------------
  async function fetchCollections() {
    try {
      const data = await getCollections({ page: 1, page_size: 100 })
      collections.value = data.items
    } catch {
      // 错误由请求拦截器统一提示
    }
  }

  async function fetchConversations() {
    loadingConversations.value = true
    try {
      const data = await listConversationsGlobal(1, 100)
      conversations.value = data.items
    } catch {
      // 错误由请求拦截器统一提示
    } finally {
      loadingConversations.value = false
    }
  }

  function removeConversationLocal(sid: string) {
    conversations.value = conversations.value.filter((c) => c.session_id !== sid)
  }

  // ---------------- 对话操作 ----------------
  function reset() {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    sessionId.value = ''
    messages.value = []
    collectionId.value = ''
    isSending.value = false
  }

  async function loadConversation(sid: string) {
    if (isSending.value) return
    sessionId.value = sid
    messages.value = []
    try {
      const detail = await getConversation(sid)
      collectionId.value = detail.collection_id || ''
      messages.value = (detail.messages || []).map((m) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        isKbAnswer: m.role === 'assistant' && !m.content.includes('⚠️'),
        showRefs: false,
        showReasoning: false,
      }))
    } catch (e) {
      console.error('加载对话失败:', e)
    }
  }

  function setCollectionId(cid: string) {
    collectionId.value = cid
  }

  function clearCollection() {
    collectionId.value = ''
  }

  /** 将引用按文档分组 */
  function groupReferences(refs: ChatReference[]): GroupedReference[] {
    const map = new Map<string, GroupedReference>()
    for (const ref of refs) {
      if (!map.has(ref.document_id)) {
        map.set(ref.document_id, {
          document_id: ref.document_id,
          document_name: ref.document_name,
          collection_id: ref.collection_id,
          collection_name: getCollectionName(ref.collection_id),
          chunks: [],
          expanded: false,
        })
      }
      map.get(ref.document_id)!.chunks.push(ref)
    }
    return Array.from(map.values()).sort((a, b) =>
      a.document_name.localeCompare(b.document_name),
    )
  }

  /**
   * 发送消息（仅负责常规问答；斜杠命令由组件层拦截处理）
   * @param text 提问内容
   * @param pushUser 是否先插入用户消息（重新生成时传 false）
   */
  async function send(text: string, pushUser = true) {
    const query = text.trim()
    if (!query || isSending.value) return

    isSending.value = true
    if (pushUser) {
      messages.value.push({ role: 'user', content: query })
    }
    messages.value.push({
      role: 'assistant',
      content: '',
      reasoning: '',
      loading: true,
      references: [],
      showRefs: false,
      showReasoning: false,
      isKbAnswer: false,
      isCommand: false,
    })
    // 取数组中的响应式代理，保证流式增量能触发视图更新
    const aiMsg = messages.value[messages.value.length - 1]

    abortController = new AbortController()

    try {
      await chatStreamGlobal(
        {
          query,
          collection_id: collectionId.value || undefined,
          session_id: sessionId.value || undefined,
          top_k: topK.value,
          temperature: temperature.value,
          max_tokens: maxTokens.value,
          use_rewrite: useRewrite.value,
          stream: streamMode.value,
        },
        (event) => {
          if (event.type === 'metadata') {
            aiMsg.references = event.references || []
            aiMsg.groupedRefs = groupReferences(event.references || [])
            aiMsg.isKbAnswer = event.is_kb_answer ?? false
            aiMsg.isCommand = event.is_command ?? false
            sessionId.value = event.session_id
          } else if (event.type === 'reasoning') {
            aiMsg.reasoning = (aiMsg.reasoning || '') + event.content
            aiMsg.showReasoning = true
          } else if (event.type === 'content') {
            aiMsg.content += event.content
            aiMsg.loading = false
          } else if (event.type === 'done') {
            aiMsg.content = stripNonKbWarning(event.answer || aiMsg.content)
            if (event.reasoning) {
              aiMsg.reasoning = event.reasoning
            }
            aiMsg.loading = false
            aiMsg.latency = event.latency_ms
            aiMsg.model = event.model
            aiMsg.isKbAnswer = event.is_kb_answer ?? aiMsg.isKbAnswer
            aiMsg.isCommand = event.is_command ?? aiMsg.isCommand
          }
        },
        abortController.signal,
      )
      // 刷新侧边栏对话列表
      fetchConversations()
    } catch (e: any) {
      if (e?.name === 'AbortError' || abortController?.signal.aborted) {
        if (!aiMsg.content) {
          aiMsg.content = '（已停止生成）'
        }
        aiMsg.loading = false
      } else {
        aiMsg.content = '抱歉，生成回答时出错，请稍后再试。'
        aiMsg.loading = false
      }
    } finally {
      isSending.value = false
      abortController = null
    }
  }

  function stop() {
    if (abortController) {
      abortController.abort()
    }
  }

  /** 重新生成第 index 条 AI 消息 */
  async function regenerate(index: number) {
    if (isSending.value) return
    // 找到该 AI 消息之前的用户消息
    let userMsgIndex = -1
    for (let i = index - 1; i >= 0; i--) {
      if (messages.value[i].role === 'user') {
        userMsgIndex = i
        break
      }
    }
    if (userMsgIndex < 0) return
    const content = messages.value[userMsgIndex].content
    messages.value = messages.value.slice(0, index)
    await send(content, false)
  }

  return {
    // state
    conversations,
    collections,
    loadingConversations,
    messages,
    sessionId,
    collectionId,
    isSending,
    streamMode,
    topK,
    temperature,
    maxTokens,
    useRewrite,
    // getters
    hasMessages,
    currentCollection,
    // actions
    getCollectionName,
    fetchCollections,
    fetchConversations,
    removeConversationLocal,
    reset,
    loadConversation,
    setCollectionId,
    clearCollection,
    groupReferences,
    send,
    stop,
    regenerate,
  }
})
