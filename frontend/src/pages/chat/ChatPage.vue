<template>
  <div class="chat-page">
    <div class="page-header mb-4 flex-between">
      <h2 class="text-xl font-bold m-0">对话验证</h2>
      <div class="flex items-center gap-2">
        <el-tag v-if="currentCollection" size="small" type="success" effect="light">
          <el-icon class="mr-1"><CollectionIcon /></el-icon>
          知识库: {{ currentCollection.name }}
        </el-tag>
        <el-tag v-else size="small" type="primary" effect="light">
          <el-icon class="mr-1"><CollectionIcon /></el-icon>
          全知识库搜索
        </el-tag>
        <el-tag v-if="sessionId" size="small">
          会话: {{ sessionId.slice(-8) }}
        </el-tag>
        <el-button size="small" :icon="RefreshLeft" @click="newConversation">
          新对话
        </el-button>
      </div>
    </div>

    <!-- 对话主区域（左侧历史 + 右侧对话） -->
    <div class="chat-main-layout">
      <!-- 左侧：历史对话列表 -->
      <div class="sidebar">
        <div class="sidebar-header">
          <el-input
            v-model="searchKeyword"
            placeholder="搜索对话..."
            size="small"
            clearable
            :prefix-icon="Search"
          />
        </div>
        <div class="conversation-list">
          <div
            v-for="conv in filteredConversations"
            :key="conv.session_id"
            class="conversation-item"
            :class="{ active: conv.session_id === sessionId }"
            @click="loadConversation(conv)"
          >
            <div class="conv-icon">
              <el-icon :size="16"><ChatDotRound /></el-icon>
            </div>
            <div class="conv-info">
              <div class="conv-title">
                {{ conv.title || '新对话' }}
              </div>
              <div class="conv-meta">
                <span v-if="conv.collection_id" class="conv-kb">
                  <el-icon :size="10"><CollectionIcon /></el-icon>
                  {{ getCollectionName(conv.collection_id) }}
                </span>
                <span v-else class="conv-kb all-kb">
                  <el-icon :size="10"><CollectionIcon /></el-icon>
                  全知识库
                </span>
                <span class="conv-time">{{ formatTime(conv.updated_at) }}</span>
              </div>
            </div>
            <el-dropdown trigger="click" @click.stop>
              <el-button size="small" text :icon="MoreFilled" class="conv-more" />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click.stop="deleteConversationItem(conv.session_id)">
                    <el-icon><Delete /></el-icon>删除
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div v-if="filteredConversations.length === 0" class="empty-conv">
            <el-icon :size="32" color="#c0c4cc"><ChatDotRound /></el-icon>
            <p class="text-gray-400 mt-2 text-sm">暂无对话记录</p>
          </div>
        </div>
      </div>

      <!-- 右侧：对话区域 -->
      <el-card class="chat-container">
        <div class="chat-layout">
          <div class="chat-main">
            <!-- 消息列表 -->
            <div ref="messageListRef" class="message-list">
              <div v-if="messages.length === 0" class="welcome-tip flex-center flex-col">
                <el-icon :size="48" color="#409eff"><ChatDotRound /></el-icon>
                <p class="mt-3 text-gray-500">你好！我是 AI 助手，请问有什么可以帮你的？</p>
                <p class="mt-1 text-gray-400 text-sm">
                  默认搜索全部知识库，输入 <b>/kb 知识库名称</b> 可以切换指定知识库，输入 <b>/help</b> 查看所有命令
                </p>
                <div class="quick-questions mt-4">
                  <el-button
                    v-for="q in quickQuestions"
                    :key="q"
                    size="small"
                    @click="sendQuickQuestion(q)"
                  >
                    {{ q }}
                  </el-button>
                </div>
                <div class="kb-select-section">
                  <div class="kb-select-label text-gray-500 text-sm mb-2">
                    当前模式：<b>搜索全部知识库</b>，也可以选择指定知识库：
                  </div>
                  <el-select
                    v-model="pendingCollection"
                    placeholder="选择指定知识库..."
                    class="w-64"
                    size="default"
                    filterable
                    clearable
                    @change="handleQuickKbSelect"
                  >
                    <el-option
                      v-for="c in collections"
                      :key="c.id"
                      :label="c.name"
                      :value="c.id"
                    />
                  </el-select>
                </div>
              </div>

              <div
                v-for="(msg, index) in messages"
                :key="index"
                class="message-item"
                :class="msg.role"
              >
                <div class="avatar">
                  <el-icon v-if="msg.role === 'user'" :size="20"><User /></el-icon>
                  <el-icon v-else :size="20"><Cpu /></el-icon>
                </div>
                <div class="message-content">
                  <div class="message-role">
                    {{ msg.role === 'user' ? '我' : 'AI 助手' }}
                    <span v-if="msg.role === 'assistant' && msg.latency" class="latency">
                      · {{ msg.latency }}ms
                    </span>
                    <span v-if="msg.role === 'assistant' && msg.model" class="latency">
                      · {{ msg.model }}
                    </span>
                    <el-tag
                      v-if="msg.isCommand"
                      size="small"
                      type="info"
                      effect="plain"
                      class="ml-2"
                    >
                      系统命令
                    </el-tag>
                  </div>

                  <!-- 回答来源标识 -->
                  <div
                    v-if="msg.role === 'assistant' && !msg.loading && msg.content && !msg.isCommand"
                    class="answer-badge-row"
                  >
                    <el-tag
                      v-if="msg.isKbAnswer"
                      size="small"
                      type="success"
                      effect="light"
                    >
                      <el-icon class="mr-1"><Reading /></el-icon>
                      知识库回答
                    </el-tag>
                    <el-tag
                      v-else
                      size="small"
                      type="warning"
                      effect="dark"
                    >
                      <el-icon class="mr-1"><Warning /></el-icon>
                      大模型直接回答
                    </el-tag>
                    <!-- 知识库切换按钮（放在回答后面，用户主动选择） -->
                    <el-button
                      v-if="!collectionId"
                      size="small"
                      text
                      type="primary"
                      class="ml-2"
                      @click="showKbSelector = true"
                    >
                      <el-icon class="mr-1"><CollectionIcon /></el-icon>
                      切换到指定知识库
                    </el-button>
                  </div>

                  <!-- 思考过程（可折叠） -->
                  <div
                    v-if="msg.role === 'assistant' && msg.reasoning"
                    class="reasoning-block"
                  >
                    <div class="reasoning-header" @click="msg.showReasoning = !msg.showReasoning">
                      <el-icon class="reasoning-icon"><MagicStick /></el-icon>
                      <span class="reasoning-label">思考过程</span>
                      <el-icon class="reasoning-toggle">
                        <component :is="msg.showReasoning ? 'ArrowDown' : 'ArrowRight'" />
                      </el-icon>
                    </div>
                    <div v-show="msg.showReasoning" class="reasoning-content markdown-content">
                      <div v-html="renderMarkdown(msg.reasoning)"></div>
                    </div>
                  </div>

                  <!-- 最终答案气泡 -->
                  <div class="message-bubble" :class="{ 'final-answer': msg.role === 'assistant', 'non-kb-bubble': msg.role === 'assistant' && !msg.isKbAnswer && !msg.isCommand, 'command-bubble': msg.isCommand }">
                    <span v-if="msg.role === 'assistant' && msg.loading" class="typing">
                      <span class="dot"></span>
                      <span class="dot"></span>
                      <span class="dot"></span>
                    </span>
                    <template v-else-if="msg.role === 'assistant'">
                      <div class="answer-header">
                        <el-icon class="answer-icon"><ChatDotRound /></el-icon>
                        <span class="answer-label">
                          {{ msg.isCommand ? '系统消息' : '最终答案' }}
                        </span>
                      </div>
                      <div class="answer-body markdown-content" v-html="renderMarkdown(msg.content)"></div>
                    </template>
                    <template v-else>
                      {{ msg.content }}
                    </template>
                  </div>

                  <!-- 非知识库回答警示（独立显示） -->
                  <div
                    v-if="msg.role === 'assistant' && !msg.loading && msg.content && !msg.isKbAnswer && collectionId && !msg.isCommand"
                    class="non-kb-warning"
                  >
                    <el-icon class="warning-icon"><WarningFilled /></el-icon>
                    <div class="warning-text">
                      <strong>非知识库回答</strong>
                      <p>当前问题未在知识库中检索到相关内容，以上回答由大模型直接生成，仅供参考，不保证准确性。</p>
                    </div>
                  </div>

                  <!-- 消息操作栏（仅 assistant 且非 loading） -->
                  <div v-if="msg.role === 'assistant' && !msg.loading && msg.content && !msg.isCommand" class="message-actions">
                    <el-button size="small" text :icon="CopyDocument" @click="copyAnswer(msg.content)">
                      复制
                    </el-button>
                    <el-button size="small" text :icon="RefreshRight" @click="regenerate(index)">
                      重新生成
                    </el-button>
                  </div>

                  <!-- 向量检索结果（按文档分类折叠） -->
                  <div
                    v-if="msg.role === 'assistant' && msg.groupedRefs && msg.groupedRefs.length > 0"
                    class="search-results"
                  >
                    <div class="search-results-header flex items-center gap-2 mb-2" @click="msg.showRefs = !msg.showRefs">
                      <el-icon color="#909399"><Search /></el-icon>
                      <span class="text-sm text-gray-500">
                        向量检索结果 ({{ msg.references?.length }} 条 · {{ msg.groupedRefs.length }} 个文档{{ !collectionId ? ' · 跨知识库' : '' }})
                      </span>
                      <el-icon class="toggle-icon">
                        <component :is="msg.showRefs ? 'ArrowDown' : 'ArrowRight'" />
                      </el-icon>
                    </div>

                    <div v-show="msg.showRefs" class="search-results-body">
                      <div
                        v-for="group in msg.groupedRefs"
                        :key="group.document_id"
                        class="doc-group"
                      >
                        <div
                          class="doc-group-header flex items-center gap-2"
                          @click="toggleDocGroup(msg, group.document_id)"
                        >
                          <el-icon class="group-toggle-icon">
                            <component :is="group.expanded ? 'FolderOpened' : 'Folder'" />
                          </el-icon>
                          <span class="doc-name text-sm font-medium">
                            {{ group.document_name }}
                          </span>
                          <el-tag
                            v-if="!collectionId && group.collection_name"
                            size="small"
                            type="primary"
                            effect="plain"
                          >
                            {{ group.collection_name }}
                          </el-tag>
                          <el-tag size="small" type="info" effect="plain">
                            {{ group.chunks.length }} 个片段
                          </el-tag>
                          <span class="doc-score text-xs">
                            最高相似度:
                            <span :style="{ color: scoreColor(Math.max(...group.chunks.map(c => c.score))) }">
                              {{ (Math.max(...group.chunks.map(c => c.score)) * 100).toFixed(1) }}%
                            </span>
                          </span>
                        </div>

                        <div v-show="group.expanded" class="doc-group-body">
                          <div
                            v-for="(chunk, cIdx) in group.chunks"
                            :key="chunk.chunk_id"
                            class="chunk-item"
                          >
                            <div class="chunk-header flex items-center gap-2">
                              <el-tag size="small" type="primary" effect="plain">
                                片段 {{ cIdx + 1 }}
                              </el-tag>
                              <el-tag v-if="chunk.page_num" size="small" effect="plain">
                                第 {{ chunk.page_num }} 页
                              </el-tag>
                              <el-tag size="small" effect="plain">Chunk {{ chunk.chunk_index }}</el-tag>
                              <span class="chunk-score" :style="{ color: scoreColor(chunk.score) }">
                                {{ (chunk.score * 100).toFixed(1) }}%
                              </span>
                            </div>
                            <div class="chunk-text">
                              {{ chunk.text }}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div ref="bottomRef"></div>
            </div>

            <!-- 输入区 -->
            <div class="input-area">
              <div class="input-toolbar flex items-center gap-2 mb-2">
                <el-tooltip content="参数设置" placement="top">
                  <el-button
                    size="small"
                    text
                    :icon="Setting"
                    @click="showSettings = !showSettings"
                  >
                    参数设置
                  </el-button>
                </el-tooltip>
                <el-tooltip :content="streamMode ? '流式输出已开启' : '流式输出已关闭'" placement="top">
                  <el-button
                    size="small"
                    text
                    :type="streamMode ? 'primary' : 'info'"
                    :icon="Lightning"
                    @click="streamMode = !streamMode"
                  >
                    流式输出
                  </el-button>
                </el-tooltip>
                <el-tooltip content="选择/切换知识库" placement="top">
                  <el-button
                    size="small"
                    text
                    type="success"
                    :icon="CollectionIcon"
                    @click="showKbSelector = true"
                  >
                    {{ collectionId ? '切换知识库' : '选择知识库' }}
                  </el-button>
                </el-tooltip>
                <span class="flex-1"></span>
                <span class="text-xs text-gray-400">
                  {{ inputText.length }} / 2000
                </span>
              </div>

              <!-- 参数设置面板 -->
              <el-collapse-transition>
                <div v-show="showSettings" class="settings-panel mb-2">
                  <el-form :inline="true" size="small" label-position="top">
                    <el-form-item label="检索数量 (Top K)">
                      <el-input-number v-model="topK" :min="1"  />
                    </el-form-item>
                    <el-form-item label="温度 (Temperature)">
                      <el-input-number
                        v-model="temperature"
                        :min="0"
                        :max="2"
                        :step="0.1"
                        :precision="1"
                      />
                    </el-form-item>
                    <el-form-item label="最大 Token 数">
                      <el-input-number v-model="maxTokens" :min="128" :max="8192" :step="128" />
                    </el-form-item>
                    <el-form-item label="问题改写">
                      <el-switch v-model="useRewrite" />
                    </el-form-item>
                  </el-form>
                </div>
              </el-collapse-transition>

              <div class="input-wrapper">
                <el-input
                  v-model="inputText"
                  type="textarea"
                  :rows="inputRows"
                  :autosize="{ minRows: 1, maxRows: 6 }"
                  placeholder="输入你的问题，按 Enter 发送，Shift+Enter 换行。输入 /help 查看命令..."
                  resize="none"
                  :disabled="isSending"
                  @keydown="handleKeydown"
                  @input="handleInputChange"
                />
                <div class="input-actions flex items-center justify-end mt-2">
                  <el-button
                    v-if="isSending && streamMode"
                    type="danger"
                    :icon="VideoPause"
                    @click="stopGeneration"
                  >
                    停止生成
                  </el-button>
                  <el-button
                    v-else
                    type="primary"
                    :icon="Promotion"
                    :loading="isSending"
                    @click="sendMessage"
                    :disabled="!inputText.trim() || isSending"
                  >
                    发送
                  </el-button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 知识库选择对话框 -->
    <el-dialog
      v-model="showKbSelector"
      title="选择知识库"
      width="500px"
      :close-on-click-modal="true"
    >
      <el-input
        v-model="kbSearchKeyword"
        placeholder="搜索知识库..."
        size="default"
        clearable
        :prefix-icon="Search"
        class="mb-3"
      />
      <div class="kb-list">
        <div
          v-for="c in filteredCollections"
          :key="c.id"
          class="kb-item"
          :class="{ active: c.id === collectionId }"
          @click="selectKb(c.id)"
        >
          <el-icon class="kb-icon" :size="20"><CollectionIcon /></el-icon>
          <div class="kb-info">
            <div class="kb-name">{{ c.name }}</div>
            <div class="kb-desc text-xs text-gray-400">
              {{ c.document_count || 0 }} 个文档
            </div>
          </div>
          <el-icon v-if="c.id === collectionId" class="check-icon" color="#67c23a"><Check /></el-icon>
        </div>
        <div v-if="filteredCollections.length === 0" class="empty-kb text-center py-8 text-gray-400">
          暂无知识库
        </div>
      </div>
      <template #footer>
        <el-button @click="clearKb">
          搜索全部知识库
        </el-button>
        <el-button type="primary" @click="showKbSelector = false">
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  User,
  Cpu,
  Link,
  ArrowDown,
  ArrowRight,
  Setting,
  Lightning,
  Promotion,
  RefreshLeft,
  RefreshRight,
  CopyDocument,
  VideoPause,
  Reading,
  Warning,
  WarningFilled,
  Search,
  Folder,
  FolderOpened,
  MagicStick,
  Collection as CollectionIcon,
  Delete,
  MoreFilled,
  Check,
} from '@element-plus/icons-vue'
import { marked } from 'marked'
import { getCollections, type Collection } from '@/api/collection'
import {
  chatStreamGlobal,
  listConversationsGlobal,
  getConversation,
  deleteConversation,
  type ChatReference,
  type Conversation,
} from '@/api/chat'

// 配置 marked
marked.setOptions({
  breaks: true,
  gfm: true,
})

const collections = ref<Collection[]>([])
const collectionId = ref('')
const pendingCollection = ref('')
const sessionId = ref('')
const inputText = ref('')
const inputRows = ref(1)
const isSending = ref(false)
const streamMode = ref(true)
const showSettings = ref(false)
const showKbSelector = ref(false)
const kbSearchKeyword = ref('')
const searchKeyword = ref('')
const abortController = ref<AbortController | null>(null)

// 参数设置
const topK = ref(5)
const temperature = ref(0.7)
const maxTokens = ref(2048)
const useRewrite = ref(true)

// 历史对话列表
const conversations = ref<Conversation[]>([])

interface ChatMessage {
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
  /** 按文档分组后的引用，用于前端展示 */
  groupedRefs?: GroupedReference[]
}

interface GroupedReference {
  document_id: string
  document_name: string
  collection_id?: string
  collection_name?: string
  chunks: ChatReference[]
  expanded: boolean
}

const messages = ref<ChatMessage[]>([])
const messageListRef = ref<HTMLElement | null>(null)
const bottomRef = ref<HTMLElement | null>(null)

const quickQuestions = [
  '介绍一下系统的主要功能',
  '什么是向量检索？',
  '如何上传文档？',
]

const currentCollection = computed(() =>
  collections.value.find((c) => c.id === collectionId.value)
)

const filteredCollections = computed(() => {
  if (!kbSearchKeyword.value) return collections.value
  const kw = kbSearchKeyword.value.toLowerCase()
  return collections.value.filter((c) =>
    c.name.toLowerCase().includes(kw)
  )
})

const filteredConversations = computed(() => {
  if (!searchKeyword.value) return conversations.value
  const kw = searchKeyword.value.toLowerCase()
  return conversations.value.filter((c) =>
    (c.title || '').toLowerCase().includes(kw)
  )
})

async function fetchCollections() {
  try {
    const data = await getCollections({ page: 1, page_size: 100 })
    collections.value = data.items
  } catch (e) {
    // error
  }
}

async function fetchConversations() {
  try {
    const data = await listConversationsGlobal(1, 50)
    conversations.value = data.items
  } catch (e) {
    console.error('Failed to fetch conversations:', e)
  }
}

function getCollectionName(cid: string | null): string {
  if (!cid) return ''
  const c = collections.value.find((x) => x.id === cid)
  return c?.name || cid.slice(0, 8)
}

function formatTime(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  return `${date.getMonth() + 1}/${date.getDate()}`
}

function newConversation() {
  sessionId.value = ''
  messages.value = []
  collectionId.value = ''
  pendingCollection.value = ''
}

async function loadConversation(conv: Conversation) {
  if (isSending.value) {
    ElMessage.warning('请等待当前对话完成')
    return
  }
  sessionId.value = conv.session_id
  collectionId.value = conv.collection_id || ''
  messages.value = []

  try {
    const detail = await getConversation(conv.session_id)
    if (detail && detail.messages) {
      messages.value = detail.messages.map((m) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        isKbAnswer: m.role === 'assistant' && !m.content.includes('⚠️'),
        showRefs: false,
        showReasoning: false,
      }))
    }
  } catch (e) {
    console.error('Failed to load conversation:', e)
    ElMessage.error('加载对话失败')
  }
  scrollToBottom()
}

async function deleteConversationItem(sid: string) {
  try {
    await ElMessageBox.confirm('确定删除这个对话吗？', '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await deleteConversation(sid)
    conversations.value = conversations.value.filter((c) => c.session_id !== sid)
    if (sessionId.value === sid) {
      newConversation()
    }
    ElMessage.success('删除成功')
  } catch {
    // 用户取消
  }
}

function selectKb(cid: string) {
  collectionId.value = cid
  showKbSelector.value = false
  ElMessage.success(`已切换到知识库：${getCollectionName(cid)}`)
}

function clearKb() {
  collectionId.value = ''
  showKbSelector.value = false
  ElMessage.info('已切换为全知识库搜索模式')
}

function handleQuickKbSelect(val: string) {
  if (val) {
    collectionId.value = val
    ElMessage.success(`已选择知识库：${getCollectionName(val)}`)
  }
}

function scrollToBottom() {
  nextTick(() => {
    bottomRef.value?.scrollIntoView({ behavior: 'smooth' })
  })
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

function handleInputChange() {
  // 简单自适应行数（按换行符估算）
  const lines = inputText.value.split('\n').length
  inputRows.value = Math.min(Math.max(lines, 1), 6)
}

function sendQuickQuestion(q: string) {
  inputText.value = q
  sendMessage()
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return marked.parse(text) as string
  } catch {
    return text
  }
}

/**
 * 从答案末尾剥离非知识库警示语（用于独立显示警示条）
 */
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

function copyAnswer(text: string) {
  navigator.clipboard.writeText(text).then(() => {
    ElMessage.success('已复制到剪贴板')
  }).catch(() => {
    ElMessage.warning('复制失败，请手动复制')
  })
}

function stopGeneration() {
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
  }
  isSending.value = false
}

function regenerate(index: number) {
  if (isSending.value) return
  // 找到该 AI 消息之前的用户消息
  let userMsgIndex = -1
  for (let i = index - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') {
      userMsgIndex = i
      break
    }
  }
  if (userMsgIndex < 0) {
    ElMessage.warning('找不到对应的用户消息')
    return
  }
  const userContent = messages.value[userMsgIndex].content
  // 移除该 AI 消息及之后的所有消息
  messages.value = messages.value.slice(0, index)
  // 重新发送
  doSend(userContent)
}

/**
 * 检测输入是否为斜杠命令（/** 格式），并处理前端命令
 * 返回 true 表示已在前端处理完毕，不需要发给后端
 */
function handleSlashCommand(text: string): boolean {
  const trimmed = text.trim()
  if (!trimmed.startsWith('/')) return false

  const parts = trimmed.split(/\s+/)
  const cmd = parts[0].toLowerCase()
  const arg = parts.slice(1).join(' ')

  // /kb 命令：弹出知识库选择器
  if (cmd === '/kb') {
    if (arg) {
      // 如果带参数，尝试匹配知识库
      const matched = collections.value.find(
        (c) => c.name.toLowerCase().includes(arg.toLowerCase()) || c.id === arg
      )
      if (matched) {
        collectionId.value = matched.id
        // 添加用户命令消息
        messages.value.push({ role: 'user', content: text })
        // 添加系统响应
        messages.value.push({
          role: 'assistant',
          content: `已切换到知识库：**${matched.name}**\n\n后续对话将基于该知识库进行检索增强回答。`,
          isKbAnswer: false,
          isCommand: true,
          showRefs: false,
          showReasoning: false,
          model: 'system',
        })
        scrollToBottom()
        return true
      } else {
        // 没找到匹配的，弹出选择器让用户选
        showKbSelector.value = true
        kbSearchKeyword.value = arg
        messages.value.push({ role: 'user', content: text })
        messages.value.push({
          role: 'assistant',
          content: `未找到名称包含「${arg}」的知识库，请在弹出的对话框中选择。`,
          isKbAnswer: false,
          isCommand: true,
          showRefs: false,
          showReasoning: false,
          model: 'system',
        })
        scrollToBottom()
        return true
      }
    } else {
      // 不带参数，直接弹出选择器
      showKbSelector.value = true
      messages.value.push({ role: 'user', content: text })
      const currentKb = currentCollection.value
        ? `当前知识库：**${currentCollection.value.name}**`
        : '当前模式：**搜索全部知识库**'
      messages.value.push({
        role: 'assistant',
        content: `${currentKb}\n\n请在弹出的对话框中选择或切换知识库。`,
        isKbAnswer: false,
        isCommand: true,
        showRefs: false,
        showReasoning: false,
        model: 'system',
      })
      scrollToBottom()
      return true
    }
  }

  // /clear 或 /reset：清空对话
  if (cmd === '/clear' || cmd === '/reset') {
    messages.value.push({ role: 'user', content: text })
    messages.value.push({
      role: 'assistant',
      content: '对话已清空，开始新的对话吧！',
      isKbAnswer: false,
      isCommand: true,
      showRefs: false,
      showReasoning: false,
      model: 'system',
    })
    scrollToBottom()
    // 延迟清空（让用户看到系统消息）
    setTimeout(() => {
      sessionId.value = ''
      messages.value = []
    }, 800)
    return true
  }

  // /help 命令：显示帮助（也发给后端，后端也会返回帮助）
  // 不拦截，交给后端处理，更准确

  return false // 其他命令交给后端处理
}

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text) return
  if (isSending.value) return

  // 先尝试前端处理命令
  const handled = handleSlashCommand(text)
  if (handled) {
    inputText.value = ''
    return
  }

  doSend(text)
  inputText.value = ''
}

async function doSend(text: string) {
  isSending.value = true

  // 添加用户消息
  messages.value.push({ role: 'user', content: text })

  // 添加加载中的 AI 消息
  const aiMsg: ChatMessage = {
    role: 'assistant',
    content: '',
    reasoning: '',
    loading: true,
    references: [],
    showRefs: false,
    showReasoning: false,
    isKbAnswer: false,
    isCommand: false,
  }
  messages.value.push(aiMsg)
  scrollToBottom()

  // 创建 AbortController 支持取消
  abortController.value = new AbortController()

  try {
    const reqData = {
      query: text,
      collection_id: collectionId.value || undefined,
      session_id: sessionId.value || undefined,
      top_k: topK.value,
      temperature: temperature.value,
      max_tokens: maxTokens.value,
      use_rewrite: useRewrite.value,
      stream: streamMode.value,
    }

    if (streamMode.value) {
      // 流式输出（使用全局接口）
      await chatStreamGlobal(
        reqData,
        (event) => {
          if (event.type === 'metadata') {
            aiMsg.references = event.references || []
            aiMsg.groupedRefs = groupReferences(event.references || [])
            aiMsg.showRefs = false
            aiMsg.isKbAnswer = event.is_kb_answer ?? false
            aiMsg.isCommand = event.is_command ?? false
            sessionId.value = event.session_id
          } else if (event.type === 'reasoning') {
            aiMsg.reasoning = (aiMsg.reasoning || '') + event.content
            aiMsg.showReasoning = true // 流式时自动展开思考过程
          } else if (event.type === 'content') {
            aiMsg.content += event.content
            aiMsg.loading = false
          } else if (event.type === 'done') {
            // 从答案中剥离警示语（如果存在），改为独立展示
            let answer = event.answer || aiMsg.content
            answer = stripNonKbWarning(answer)
            aiMsg.content = answer
            if (event.reasoning) {
              aiMsg.reasoning = event.reasoning
            }
            aiMsg.loading = false
            aiMsg.latency = event.latency_ms
            aiMsg.model = event.model
            aiMsg.isKbAnswer = event.is_kb_answer ?? aiMsg.isKbAnswer
            aiMsg.isCommand = event.is_command ?? aiMsg.isCommand
          }
          scrollToBottom()
        },
        abortController.value.signal,
      )
    } else {
      // 非流式（使用全局接口 - 暂时用不到，先留着）
      // 由于我们的非流式全局接口叫 chatAskGlobal
      // 但为了简化，我们主要用流式，这里先跳过非流式全局
      // 实际上 chat.ts 中还没加 chatAskGlobal，需要的话后面加
      ElMessage.warning('非流式模式暂不支持全局接口，请使用流式模式')
      aiMsg.content = '非流式模式暂不支持全局接口，请使用流式模式'
      aiMsg.loading = false
    }

    // 刷新对话列表
    fetchConversations()
  } catch (e: any) {
    // 如果是主动取消，不显示错误
    if (e?.name === 'AbortError' || abortController.value?.signal.aborted) {
      if (!aiMsg.content) {
        aiMsg.content = '（已停止生成）'
      }
      aiMsg.loading = false
    } else {
      aiMsg.content = '抱歉，生成回答时出错，请稍后再试。'
      aiMsg.loading = false
      ElMessage.error(e.message || '请求失败')
    }
  } finally {
    isSending.value = false
    abortController.value = null
    scrollToBottom()
  }
}

function scoreColor(score: number) {
  if (score >= 0.8) return '#67c23a'
  if (score >= 0.6) return '#e6a23c'
  return '#f56c6c'
}

/**
 * 将引用按文档分组，用于分类展示
 */
function groupReferences(refs: ChatReference[]): GroupedReference[] {
  const map = new Map<string, GroupedReference>()
  for (const ref of refs) {
    if (!map.has(ref.document_id)) {
      const coll = collections.value.find((c) => c.id === ref.collection_id)
      map.set(ref.document_id, {
        document_id: ref.document_id,
        document_name: ref.document_name,
        collection_id: ref.collection_id,
        collection_name: coll?.name,
        chunks: [],
        expanded: false,
      })
    }
    map.get(ref.document_id)!.chunks.push(ref)
  }
  // 按文档名排序
  return Array.from(map.values()).sort((a, b) => a.document_name.localeCompare(b.document_name))
}

/**
 * 切换某文档分组的展开状态
 */
function toggleDocGroup(msg: ChatMessage, docId: string) {
  const group = msg.groupedRefs?.find((g) => g.document_id === docId)
  if (group) {
    group.expanded = !group.expanded
  }
}

onMounted(() => {
  fetchCollections()
  fetchConversations()
})
</script>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.page-header {
  flex-shrink: 0;
}

/* ===== 主布局：左侧历史 + 右侧对话 ===== */
.chat-main-layout {
  flex: 1;
  display: flex;
  gap: 16px;
  min-height: 0;
}

/* ===== 左侧边栏：历史对话 ===== */
.sidebar {
  width: 260px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-header {
  padding: 12px;
  border-bottom: 1px solid #ebeef5;
  flex-shrink: 0;
}

.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conversation-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
  margin-bottom: 4px;
  position: relative;
}

.conversation-item:hover {
  background: #f5f7fa;
}

.conversation-item.active {
  background: #ecf5ff;
  border-left: 3px solid #409eff;
  padding-left: 9px;
}

.conv-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, #409eff, #66b1ff);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.conv-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #909399;
  flex-wrap: wrap;
}

.conv-kb {
  display: flex;
  align-items: center;
  gap: 2px;
  color: #67c23a;
}

.conv-kb.all-kb {
  color: #409eff;
}

.conv-time {
  color: #c0c4cc;
}

.conv-more {
  opacity: 0;
  transition: opacity 0.2s;
  flex-shrink: 0;
}

.conversation-item:hover .conv-more {
  opacity: 1;
}

.empty-conv {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
}

/* ===== 右侧对话区 ===== */
.chat-container {
  flex: 1;
  padding: 0;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-container :deep(.el-card__body) {
  padding: 0;
  height: 100%;
}

.chat-layout {
  display: flex;
  height: calc(100vh - 180px);
  min-height: 500px;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #f9fafb;
}

.welcome-tip {
  height: 100%;
  min-height: 300px;
  color: #909399;
}

.quick-questions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.kb-select-section {
  margin-top: 24px;
  padding: 16px 20px;
  background: #f5f7fa;
  border-radius: 8px;
  border: 1px dashed #dcdfe6;
}

.kb-select-label {
  font-weight: 500;
}

.message-item {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.message-item.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.message-item.user .avatar {
  background: #67c23a;
}

.message-content {
  max-width: 75%;
}

.message-item.user .message-content {
  text-align: right;
}

.message-role {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.message-item.user .message-role {
  text-align: right;
}

.latency {
  color: #c0c4cc;
  font-size: 11px;
  margin-left: 4px;
}

.message-bubble {
  display: inline-block;
  padding: 10px 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #ebeef5;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-size: 14px;
  text-align: left;
  max-width: 100%;
}

.message-item.user .message-bubble {
  background: #ecf5ff;
  border-color: #d9ecff;
}

/* Markdown 样式 */
.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3) {
  margin: 10px 0 8px;
  font-weight: 600;
}

.markdown-content :deep(h1) { font-size: 18px; }
.markdown-content :deep(h2) { font-size: 16px; }
.markdown-content :deep(h3) { font-size: 15px; }

.markdown-content :deep(p) {
  margin: 6px 0;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  padding-left: 24px;
  margin: 6px 0;
}

.markdown-content :deep(li) {
  margin: 2px 0;
}

.markdown-content :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
  font-family: 'Consolas', 'Monaco', monospace;
  color: #e74c3c;
}

.markdown-content :deep(pre) {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 8px 0;
}

.markdown-content :deep(pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
}

.markdown-content :deep(blockquote) {
  border-left: 4px solid #409eff;
  padding-left: 12px;
  margin: 8px 0;
  color: #606266;
  background: #ecf5ff;
  border-radius: 0 6px 6px 0;
}

.markdown-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid #ebeef5;
  padding: 6px 10px;
  text-align: left;
}

.markdown-content :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}

.markdown-content :deep(a) {
  color: #409eff;
  text-decoration: none;
}

.markdown-content :deep(a:hover) {
  text-decoration: underline;
}

.typing {
  display: inline-flex;
  gap: 4px;
  padding: 4px 0;
}

.typing .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: blink 1.4s infinite both;
}

.typing .dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing .dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes blink {
  0%, 80%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1);
  }
}

.message-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

.message-item.user .message-actions {
  justify-content: flex-end;
}

/* ---------- 回答来源徽章 ---------- */
.answer-badge-row {
  margin-bottom: 6px;
  text-align: left;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

/* ---------- 思考过程 ---------- */
.reasoning-block {
  margin-bottom: 8px;
  background: #f5f7fa;
  border: 1px dashed #dcdfe6;
  border-radius: 8px;
  overflow: hidden;
  text-align: left;
}

.reasoning-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  background: #fafafa;
  font-size: 13px;
  color: #909399;
  transition: background 0.2s;
}

.reasoning-header:hover {
  background: #f0f2f5;
}

.reasoning-icon {
  color: #909399;
  font-size: 14px;
}

.reasoning-label {
  font-weight: 500;
  color: #606266;
  font-size: 13px;
}

.reasoning-toggle {
  margin-left: auto;
  color: #c0c4cc;
  font-size: 12px;
}

.reasoning-content {
  padding: 10px 14px;
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  max-height: 400px;
  overflow-y: auto;
  background: #fff;
  border-top: 1px dashed #ebeef5;
}

.reasoning-content :deep(p) {
  margin: 4px 0;
  opacity: 0.85;
}

/* ---------- 最终答案气泡 ---------- */
.message-bubble.final-answer {
  border-left: 4px solid #409eff;
  position: relative;
  padding-top: 10px;
}

.message-bubble.command-bubble {
  border-left-color: #909399 !important;
  background: #fafafa !important;
}

.answer-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid #ebeef5;
}

.answer-icon {
  color: #409eff;
  font-size: 16px;
}

.answer-label {
  font-weight: 600;
  color: #303133;
  font-size: 13px;
}

.command-bubble .answer-icon {
  color: #909399;
}

.answer-body {
  font-size: 14px;
}

/* ---------- 非知识库回答警示条 ---------- */
.non-kb-warning {
  display: flex;
  gap: 10px;
  margin-top: 8px;
  padding: 10px 12px;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
  border-radius: 8px;
  text-align: left;
}

.warning-icon {
  color: #e6a23c;
  font-size: 18px;
  flex-shrink: 0;
  margin-top: 1px;
}

.warning-text {
  flex: 1;
  font-size: 13px;
  color: #606266;
  line-height: 1.5;
}

.warning-text strong {
  color: #e6a23c;
  font-size: 13px;
  display: block;
  margin-bottom: 2px;
}

.warning-text p {
  margin: 0;
  color: #909399;
  font-size: 12px;
}

/* ---------- 非知识库回答气泡 ---------- */
.non-kb-bubble {
  border-left-color: #e6a23c !important;
  background: #fffbf5 !important;
}

.non-kb-bubble .answer-icon {
  color: #e6a23c;
}

.non-kb-bubble .answer-header {
  border-bottom-color: #f5dab1;
}

/* ---------- 向量检索结果（整体区） ---------- */
.search-results {
  margin-top: 10px;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 12px;
  text-align: left;
  color: #606266;
}

.search-results-header {
  cursor: pointer;
  user-select: none;
}

.search-results-header .toggle-icon {
  color: #c0c4cc;
  transition: transform 0.2s;
}

.search-results-body {
  margin-top: 6px;
}

/* ---------- 文档分组 ---------- */
.doc-group {
  margin-bottom: 6px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  overflow: hidden;
}

.doc-group:last-child {
  margin-bottom: 0;
}

.doc-group-header {
  padding: 8px 10px;
  background: #fafafa;
  cursor: pointer;
  user-select: none;
  flex-wrap: wrap;
  gap: 6px !important;
}

.doc-group-header:hover {
  background: #f5f7fa;
}

.group-toggle-icon {
  color: #909399;
  font-size: 16px;
}

.doc-name {
  color: #303133;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-score {
  margin-left: auto;
  color: #909399;
}

.doc-score span {
  font-weight: 600;
  margin-left: 4px;
}

.doc-group-body {
  padding: 6px 10px 10px;
}

/* ---------- 片段项 ---------- */
.chunk-item {
  padding: 8px;
  background: #fafafa;
  border-radius: 4px;
  margin-top: 6px;
  border: 1px dashed #dcdfe6;
}

.chunk-item:first-child {
  margin-top: 4px;
}

.chunk-header {
  flex-wrap: wrap;
  gap: 4px !important;
  margin-bottom: 4px;
}

.chunk-score {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
}

.chunk-text {
  font-size: 13px;
  color: #606266;
  line-height: 1.5;
  max-height: 100px;
  overflow-y: auto;
  white-space: pre-wrap;
  padding: 4px 2px;
}

.input-area {
  border-top: 1px solid #ebeef5;
  padding: 12px 16px;
  background: #fff;
  flex-shrink: 0;
}

.settings-panel {
  background: #f5f7fa;
  padding: 10px 12px;
  border-radius: 8px;
}

.input-wrapper :deep(.el-textarea__inner) {
  font-size: 14px;
  line-height: 1.6;
}

.input-actions {
  margin-top: 8px;
}

/* ---------- 知识库选择对话框 ---------- */
.kb-list {
  max-height: 400px;
  overflow-y: auto;
}

.kb-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s;
  margin-bottom: 6px;
  border: 1px solid transparent;
}

.kb-item:hover {
  background: #f5f7fa;
}

.kb-item.active {
  background: #ecf5ff;
  border-color: #409eff;
}

.kb-icon {
  color: #409eff;
  flex-shrink: 0;
}

.kb-info {
  flex: 1;
  min-width: 0;
}

.kb-name {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 2px;
}

.check-icon {
  flex-shrink: 0;
  font-size: 18px;
}

.empty-kb {
  color: #c0c4cc;
}

.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.flex-center {
  display: flex;
  justify-content: center;
  align-items: center;
}

.flex-col {
  flex-direction: column;
}
</style>
