<template>
  <div class="chat-root">
    <!-- 顶部栏 -->
    <div class="chat-topbar">
      <div class="chat-title">
        <span class="live-dot"></span>
        <span>智能对话</span>
      </div>
      <div class="chat-top-actions">
        <div class="chip accent" @click="showKbSelector = true">
          <el-icon :size="14"><CollectionIcon /></el-icon>
          <span>{{ chat.currentCollection ? chat.currentCollection.name : '全知识库' }}</span>
        </div>
        <button class="chip" @click="newConversation">
          <el-icon :size="14"><RefreshLeft /></el-icon>
          <span>新对话</span>
        </button>
      </div>
    </div>

    <!-- 主体 -->
    <div class="chat-body">
      <div ref="messageListRef" class="message-scroll">
        <!-- 空态 -->
        <div v-if="!chat.hasMessages" class="hero">
          <div class="hero-orb">
            <svg viewBox="0 0 48 48" width="34" height="34" aria-hidden="true">
              <path
                d="M24 4 41 14v20L24 44 7 34V14z"
                fill="none"
                stroke="#ffffff"
                stroke-width="2.2"
                stroke-linejoin="round"
                opacity="0.9"
              />
              <circle cx="24" cy="24" r="5.2" fill="#ffffff" />
              <circle cx="24" cy="24" r="10.4" fill="none" stroke="rgba(255,255,255,.6)" stroke-width="1.3" />
            </svg>
          </div>
          <h1 class="hero-title">{{ APP_NAME }}</h1>
          <p class="hero-sub">向量知识库智能助手 · 基于检索增强生成，回答可溯源</p>

          <div class="hero-cards">
            <button v-for="q in quickQuestions" :key="q" class="hero-card" @click="sendQuickQuestion(q)">
              <span class="hero-card-icon"><el-icon :size="15"><MagicStick /></el-icon></span>
              <span class="hero-card-text">{{ q }}</span>
            </button>
          </div>

          <div class="hero-kb">
            <span class="hero-kb-label">检索范围</span>
            <el-select
              v-model="pendingCollection"
              placeholder="全部知识库"
              class="hero-kb-select"
              filterable
              clearable
              @change="handleQuickKbSelect"
            >
              <el-option v-for="c in chat.collections" :key="c.id" :label="c.name" :value="c.id" />
            </el-select>
          </div>
        </div>

        <!-- 消息流 -->
        <div v-else class="stream">
          <div
            v-for="(msg, index) in chat.messages"
            :key="index"
            class="msg"
            :class="msg.role"
          >
            <div class="msg-avatar">
              <el-icon v-if="msg.role === 'user'" :size="16"><User /></el-icon>
              <el-icon v-else :size="16"><Cpu /></el-icon>
            </div>

            <div class="msg-body">
              <!-- 用户消息 -->
              <template v-if="msg.role === 'user'">
                <div class="bubble-user">{{ msg.content }}</div>
              </template>

              <!-- AI 消息 -->
              <template v-else>
                <div class="msg-head">
                  <span class="who">小叶</span>
                  <span v-if="msg.latency" class="meta-dot">{{ msg.latency }}ms</span>
                  <span v-if="msg.model" class="meta-dot">{{ msg.model }}</span>
                  <span v-if="msg.isCommand" class="meta-tag">系统消息</span>
                </div>

                <!-- 来源徽章（知识库命中与否已知后才渲染，避免流式期间误判） -->
                <div v-if="!msg.loading && msg.content && !msg.isCommand && msg.kbKnown" class="badge-row">
                  <span v-if="msg.isKbAnswer" class="badge ok">
                    <el-icon :size="12"><Reading /></el-icon>知识库回答
                  </span>
                  <span v-else class="badge warn">
                    <el-icon :size="12"><Warning /></el-icon>大模型直接回答
                  </span>
                  <button v-if="!chat.collectionId" class="badge-link" @click="showKbSelector = true">
                    <el-icon :size="12"><CollectionIcon /></el-icon>切换到指定知识库
                  </button>
                </div>

                <!-- 思考过程 -->
                <div v-if="msg.reasoning" class="reasoning">
                  <div class="reasoning-head" @click="msg.showReasoning = !msg.showReasoning">
                    <el-icon class="reasoning-icon" :size="14"><MagicStick /></el-icon>
                    <span>思考过程</span>
                    <el-icon class="reasoning-toggle" :size="13">
                      <component :is="msg.showReasoning ? 'ArrowDown' : 'ArrowRight'" />
                    </el-icon>
                  </div>
                  <div v-show="msg.showReasoning" class="reasoning-body markdown-content" v-html="renderMarkdown(msg.reasoning)"></div>
                </div>

                <!-- 答案面板 -->
                <div class="answer-panel" :class="{ 'is-warn': !msg.isKbAnswer && !msg.isCommand && !msg.loading && msg.kbKnown, 'is-cmd': msg.isCommand }">
                  <div v-if="showTyping(msg)" class="typing">
                    <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                    <span v-if="msg.stage" class="typing-stage">{{ stageLabel(msg.stage) }}</span>
                  </div>
                  <template v-else>
                    <div class="answer-head">
                      <el-icon :size="15"><ChatDotRound /></el-icon>
                      <span>{{ msg.isCommand ? '系统消息' : '最终答案' }}</span>
                    </div>
                    <div class="answer-body markdown-content" v-html="answerHtml(msg)"></div>
                  </template>
                </div>

                <!-- 非知识库警示 -->
                <div
                  v-if="!msg.loading && msg.content && !msg.isKbAnswer && chat.collectionId && !msg.isCommand && msg.kbKnown"
                  class="warn-panel"
                >
                  <el-icon class="warn-icon" :size="17"><WarningFilled /></el-icon>
                  <div>
                    <strong>非知识库回答</strong>
                    <p>当前问题未在知识库中检索到相关内容，以上回答由大模型直接生成，仅供参考，不保证准确性。</p>
                  </div>
                </div>

                <!-- 操作栏 -->
                <div v-if="!msg.loading && msg.content && !msg.isCommand" class="acts">
                  <button class="act" @click="copyAnswer(msg.content)">
                    <el-icon :size="13"><CopyDocument /></el-icon>复制
                  </button>
                  <button class="act" @click="regenerate(index)">
                    <el-icon :size="13"><RefreshRight /></el-icon>重新生成
                  </button>
                </div>

                <!-- 引用结果 -->
                <div v-if="msg.groupedRefs && msg.groupedRefs.length" class="refs">
                  <div class="refs-head" @click="msg.showRefs = !msg.showRefs">
                    <el-icon :size="14"><Search /></el-icon>
                    <span>
                      向量检索结果 ({{ msg.references?.length }} 条 · {{ msg.groupedRefs.length }} 个文档{{ !chat.collectionId ? ' · 跨知识库' : '' }})
                    </span>
                    <el-icon class="refs-toggle" :size="13">
                      <component :is="msg.showRefs ? 'ArrowDown' : 'ArrowRight'" />
                    </el-icon>
                  </div>

                  <div v-show="msg.showRefs" class="refs-body">
                    <div v-for="group in msg.groupedRefs" :key="group.document_id" class="doc-group">
                      <div class="doc-head" @click="toggleDocGroup(msg, group.document_id)">
                        <el-icon :size="15">
                          <component :is="group.expanded ? 'FolderOpened' : 'Folder'" />
                        </el-icon>
                        <span class="doc-name">{{ group.document_name }}</span>
                        <span v-if="!chat.collectionId && group.collection_name" class="mini-tag accent">{{ group.collection_name }}</span>
                        <span class="mini-tag">{{ group.chunks.length }} 片段</span>
                        <span class="doc-score">
                          最高相似度
                          <b :style="{ color: scoreColor(Math.max(...group.chunks.map((c) => c.score))) }">
                            {{ (Math.max(...group.chunks.map((c) => c.score)) * 100).toFixed(1) }}%
                          </b>
                        </span>
                      </div>

                      <div v-show="group.expanded" class="doc-body">
                        <div v-for="(chunk, cIdx) in group.chunks" :key="chunk.chunk_id" class="chunk">
                          <div class="chunk-head">
                            <span class="mini-tag accent">片段 {{ cIdx + 1 }}</span>
                            <span v-if="chunk.page_num" class="mini-tag">第 {{ chunk.page_num }} 页</span>
                            <span class="mini-tag">Chunk {{ chunk.chunk_index }}</span>
                            <span class="chunk-score" :style="{ color: scoreColor(chunk.score) }">
                              {{ (chunk.score * 100).toFixed(1) }}%
                            </span>
                          </div>
                          <div class="chunk-text">{{ chunk.text }}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="composer">
        <div class="composer-inner">
          <div class="composer-bar">
            <button class="tool" :class="{ on: showSettings }" @click="showSettings = !showSettings">
              <el-icon :size="14"><Setting /></el-icon>参数
            </button>
            <button class="tool" :class="{ on: chat.streamMode }" @click="chat.streamMode = !chat.streamMode">
              <el-icon :size="14"><Lightning /></el-icon>流式输出
            </button>
            <button class="tool" @click="showKbSelector = true">
              <el-icon :size="14"><CollectionIcon /></el-icon>
              {{ chat.collectionId ? '切换知识库' : '选择知识库' }}
            </button>
            <span class="composer-spacer"></span>
            <span class="counter">{{ inputText.length }} / 2000</span>
          </div>

          <transition name="el-collapse-transition">
            <div v-show="showSettings" class="settings">
              <label class="set-item">
                <span>检索数量 Top K</span>
                <el-input-number v-model="chat.topK" :min="1" :max="50" size="small" />
              </label>
              <label class="set-item">
                <span>温度 Temperature</span>
                <el-input-number v-model="chat.temperature" :min="0" :max="2" :step="0.1" :precision="1" size="small" />
              </label>
              <label class="set-item">
                <span>最大 Token</span>
                <el-input-number v-model="chat.maxTokens" :min="128" :max="8192" :step="128" size="small" />
              </label>
              <label class="set-item switch">
                <span>问题改写</span>
                <el-switch v-model="chat.useRewrite" />
              </label>
            </div>
          </transition>

          <div class="composer-box">
            <el-input
              v-model="inputText"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 6 }"
              placeholder="输入你的问题，Enter 发送 / Shift+Enter 换行，输入 /help 查看命令"
              resize="none"
              :disabled="chat.isSending"
              @keydown="handleKeydown"
            />
            <button
              v-if="chat.isSending && chat.streamMode"
              class="composer-send stop"
              title="停止生成"
              @click="chat.stop()"
            >
              <el-icon :size="17"><VideoPause /></el-icon>
            </button>
            <button
              v-else
              class="composer-send"
              title="发送"
              :disabled="!inputText.trim() || chat.isSending"
              @click="sendMessage"
            >
              <el-icon :size="17"><Promotion /></el-icon>
            </button>
          </div>
          <p class="composer-hint">回答由 AI 生成，知识库回答可点击「向量检索结果」查看原文片段</p>
        </div>
      </div>
    </div>

    <!-- 知识库选择 -->
    <el-dialog v-model="showKbSelector" title="选择知识库" width="500px">
      <el-input
        v-model="kbSearchKeyword"
        placeholder="搜索知识库..."
        :prefix-icon="Search"
        clearable
        class="mb-3"
      />
      <div class="kb-list">
        <div
          v-for="c in filteredCollections"
          :key="c.id"
          class="kb-item"
          :class="{ active: c.id === chat.collectionId }"
          @click="selectKb(c.id)"
        >
          <el-icon class="kb-icon" :size="18"><CollectionIcon /></el-icon>
          <div class="kb-info">
            <div class="kb-name">{{ c.name }}</div>
            <div class="kb-desc">{{ c.document_count || 0 }} 个文档</div>
          </div>
          <el-icon v-if="c.id === chat.collectionId" class="check-icon" :size="16"><Check /></el-icon>
        </div>
        <div v-if="filteredCollections.length === 0" class="kb-empty">暂无知识库</div>
      </div>
      <template #footer>
        <el-button @click="clearKb">搜索全部知识库</el-button>
        <el-button type="primary" @click="showKbSelector = false">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import aiLoading from '@/assets/images/ai_loading.gif'
import {
  ChatDotRound,
  User,
  Cpu,
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
  Check,
} from '@element-plus/icons-vue'
import { marked } from 'marked'
import { useChatStore, type ChatMessage } from '@/stores/chat'
import { APP_NAME } from '@/config/app'

marked.setOptions({ breaks: true, gfm: true })

const route = useRoute()
const router = useRouter()
const chat = useChatStore()

const inputText = ref('')
const showSettings = ref(false)
const showKbSelector = ref(false)
const kbSearchKeyword = ref('')
const pendingCollection = ref('')

const messageListRef = ref<HTMLElement | null>(null)

const quickQuestions = [
  '介绍一下系统的主要功能',
  '什么是向量检索？',
  '如何上传文档？',
]

const filteredCollections = computed(() => {
  if (!kbSearchKeyword.value) return chat.collections
  const kw = kbSearchKeyword.value.toLowerCase()
  return chat.collections.filter((c) => c.name.toLowerCase().includes(kw))
})

/** 是否贴近底部（用于流式输出时自动跟随，避免打断用户上翻查看） */
function isNearBottom(el: HTMLElement) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 140
}

/** 滚动到底部；用户已上翻查看历史时不强制跟随 */
function scrollToBottom(force = false) {
  const el = messageListRef.value
  if (!force && el && !isNearBottom(el)) return
  nextTick(() => {
    const target = messageListRef.value
    if (target) target.scrollTop = target.scrollHeight
  })
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return marked.parse(text) as string
  } catch {
    return text
  }
}

const STAGE_LABELS: Record<string, string> = {
  rewrite: '正在理解问题…',
  route: '正在分析问题类型…',
  retrieve: '正在检索知识库…',
  synthesize: '正在生成回答…',
}

/** 流式阶段提示文案 */
function stageLabel(stage: string) {
  return STAGE_LABELS[stage] || '处理中…'
}

/** 是否显示“正在输入”三点：等待首个内容，或已收到内容但还没开始逐字展示 */
function showTyping(msg: ChatMessage) {
  return !!msg.loading || (msg.display !== undefined && msg.display === '')
}

/** 是否正在逐字输出（尚未追平完整内容） */
function isTypingOut(msg: ChatMessage) {
  return msg.display !== undefined && msg.display.length < msg.content.length
}

/** 答案 HTML：逐字输出时在末尾追加闪烁光标 */
function answerHtml(msg: ChatMessage): string {
  const text = msg.display !== undefined ? msg.display : msg.content
  const html = renderMarkdown(text)
  return isTypingOut(msg) ? `${html}<span class="typing-caret"></span>` : html
}

function scoreColor(score: number) {
  if (score >= 0.8) return '#2fd39f'
  if (score >= 0.6) return '#f0b24a'
  return '#ff6b6b'
}

function toggleDocGroup(msg: ChatMessage, docId: string) {
  const group = msg.groupedRefs?.find((g) => g.document_id === docId)
  if (group) group.expanded = !group.expanded
}

function copyAnswer(text: string) {
  navigator.clipboard
    .writeText(text)
    .then(() => ElMessage.success('已复制到剪贴板'))
    .catch(() => ElMessage.warning('复制失败，请手动复制'))
}

function regenerate(index: number) {
  if (chat.isSending) return
  chat.regenerate(index)
}

function newConversation() {
  chat.reset()
  if (route.query.session) router.replace({ path: '/chat' })
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

function sendQuickQuestion(q: string) {
  inputText.value = q
  sendMessage()
}

function selectKb(cid: string) {
  chat.setCollectionId(cid)
  showKbSelector.value = false
  ElMessage.success(`已切换到知识库：${chat.getCollectionName(cid)}`)
}

function clearKb() {
  chat.clearCollection()
  showKbSelector.value = false
  ElMessage.info('已切换为全知识库搜索模式')
}

function handleQuickKbSelect(val: string) {
  if (val) {
    chat.setCollectionId(val)
    ElMessage.success(`已选择知识库：${chat.getCollectionName(val)}`)
  } else {
    chat.clearCollection()
  }
}

/** 处理斜杠命令，返回 true 表示已在前端处理 */
function handleSlashCommand(text: string): boolean {
  const trimmed = text.trim()
  if (!trimmed.startsWith('/')) return false

  const parts = trimmed.split(/\s+/)
  const cmd = parts[0].toLowerCase()
  const arg = parts.slice(1).join(' ')

  if (cmd === '/kb') {
    chat.messages.push({ role: 'user', content: text })
    if (arg) {
      const matched = chat.collections.find(
        (c) => c.name.toLowerCase().includes(arg.toLowerCase()) || c.id === arg,
      )
      if (matched) {
        chat.setCollectionId(matched.id)
        chat.messages.push({
          role: 'assistant',
          content: `已切换到知识库：**${matched.name}**\n\n后续对话将基于该知识库进行检索增强回答。`,
          isKbAnswer: false,
          isCommand: true,
          showRefs: false,
          showReasoning: false,
          model: 'system',
        })
      } else {
        showKbSelector.value = true
        kbSearchKeyword.value = arg
        chat.messages.push({
          role: 'assistant',
          content: `未找到名称包含「${arg}」的知识库，请在弹出的对话框中选择。`,
          isKbAnswer: false,
          isCommand: true,
          showRefs: false,
          showReasoning: false,
          model: 'system',
        })
      }
    } else {
      showKbSelector.value = true
      const cur = chat.currentCollection
        ? `当前知识库：**${chat.currentCollection.name}**`
        : '当前模式：**搜索全部知识库**'
      chat.messages.push({
        role: 'assistant',
        content: `${cur}\n\n请在弹出的对话框中选择或切换知识库。`,
        isKbAnswer: false,
        isCommand: true,
        showRefs: false,
        showReasoning: false,
        model: 'system',
      })
    }
    scrollToBottom()
    return true
  }

  if (cmd === '/clear' || cmd === '/reset') {
    chat.messages.push({ role: 'user', content: text })
    chat.messages.push({
      role: 'assistant',
      content: '对话已清空，开始新的对话吧！',
      isKbAnswer: false,
      isCommand: true,
      showRefs: false,
      showReasoning: false,
      model: 'system',
    })
    scrollToBottom()
    setTimeout(() => {
      chat.reset()
      if (route.query.session) router.replace({ path: '/chat' })
    }, 800)
    return true
  }

  // /help 交给后端处理
  return false
}

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || chat.isSending) return

  if (handleSlashCommand(text)) {
    inputText.value = ''
    return
  }

  inputText.value = ''
  await chat.send(text)
}

// 会话建立后同步到 URL，便于侧边栏高亮与分享
watch(
  () => chat.sessionId,
  (sid) => {
    if (!sid) return
    if (route.name === 'Chat' && route.query.session !== sid) {
      router.replace({ path: '/chat', query: { session: sid } })
    }
  },
)

// URL 会话变化（侧边栏点击 / 浏览器前进后退）
watch(
  () => route.query.session,
  (val) => {
    if (route.name !== 'Chat') return
    const sid = (val as string) || ''
    if (sid && sid === chat.sessionId) return
    if (!sid && !chat.sessionId) return
    if (sid) chat.loadConversation(sid)
    else chat.reset()
  },
)

watch(() => chat.messages.length, () => scrollToBottom())
watch(
  () => {
    const m = chat.messages[chat.messages.length - 1]
    return m ? (m.display !== undefined ? m.display : m.content) : ''
  },
  () => scrollToBottom(),
)

onMounted(async () => {
  await chat.fetchCollections()
  chat.fetchConversations()
  const sid = route.query.session as string | undefined
  if (sid && sid !== chat.sessionId) {
    await chat.loadConversation(sid)
  }
  scrollToBottom(true)
})
</script>

<style scoped>
.chat-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  position: relative;
}

/* ==================== 顶部栏 ==================== */
.chat-topbar {
  flex-shrink: 0;
  height: 58px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 26px;
}
.chat-title {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.4px;
}
.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22d3ee;
  box-shadow: 0 0 10px #22d3ee;
  animation: vdm-pulse 2s ease-in-out infinite;
}
.chat-top-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 9px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 31px;
  padding: 0 13px;
  border-radius: 999px;
  font-size: 12.5px;
  color: var(--vdm-text-dim);
  background: rgba(122, 162, 255, 0.07);
  border: 1px solid var(--vdm-border);
  cursor: pointer;
  transition: all 0.16s ease;
  max-width: 220px;
  overflow: hidden;
  white-space: nowrap;
}
.chip:hover {
  color: #fff;
  border-color: var(--vdm-border-strong);
  background: rgba(122, 162, 255, 0.14);
}
.chip.accent {
  color: #d5e3ff;
  background: linear-gradient(135deg, rgba(61, 125, 255, 0.22), rgba(34, 211, 238, 0.12));
  border-color: rgba(61, 125, 255, 0.4);
}

/* ==================== 主体 ==================== */
.chat-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.message-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 6px 0 18px;
}

/* ---------- 空态 ---------- */
.hero {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px;
  text-align: center;
}
.hero-orb {
  width: 84px;
  height: 84px;
  border-radius: 26px;
  display: grid;
  place-items: center;
  margin-bottom: 18px;
  background: linear-gradient(135deg, #3d7dff, #22d3ee);
  box-shadow: 0 0 46px rgba(61, 125, 255, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.35);
  animation: vdm-float 5s ease-in-out infinite;
}
@keyframes vdm-float {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-9px);
  }
}
.hero-title {
  margin: 0;
  font-family: var(--vdm-font-display);
  font-size: 32px;
  font-weight: 800;
  letter-spacing: 8px;
  text-indent: 8px;
  background: linear-gradient(100deg, #ffffff, #a9c6ff 45%, #22d3ee);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.hero-sub {
  margin: 6px 0 0;
  font-size: 13.5px;
  color: var(--vdm-text-dim);
}
.hero-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
  margin-top: 30px;
  width: 100%;
  max-width: 700px;
}
.hero-card {
  display: flex;
  align-items: center;
  gap: 11px;
  text-align: left;
  padding: 15px 16px;
  border-radius: 14px;
  background: var(--vdm-panel);
  border: 1px solid var(--vdm-border);
  color: var(--vdm-text-dim);
  font-size: 13.5px;
  cursor: pointer;
  backdrop-filter: blur(12px);
  transition: all 0.2s ease;
}
.hero-card:hover {
  transform: translateY(-2px);
  color: #fff;
  border-color: var(--vdm-border-strong);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.4), var(--vdm-glow);
}
.hero-card-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 9px;
  color: #cfe0ff;
  background: linear-gradient(135deg, rgba(61, 125, 255, 0.35), rgba(34, 211, 238, 0.2));
}
.hero-kb {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 26px;
  padding: 12px 18px;
  border-radius: 14px;
  background: rgba(122, 162, 255, 0.05);
  border: 1px dashed var(--vdm-border-strong);
}
.hero-kb-label {
  font-size: 12.5px;
  color: var(--vdm-text-mute);
}
.hero-kb-select {
  width: 230px;
}

/* ---------- 消息流 ---------- */
.stream {
  width: 100%;
  max-width: 940px;
  margin: 0 auto;
  padding: 6px 26px;
}
.msg {
  display: flex;
  gap: 14px;
  margin-bottom: 28px;
  animation: vdm-rise 0.36s ease both;
}
.msg.user {
  flex-direction: row-reverse;
}
.msg-avatar {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 12px;
  display: grid;
  place-items: center;
}
.msg.assistant .msg-avatar {
  color: #fff;
  background: linear-gradient(135deg, rgba(61, 125, 255, 0.95), rgba(34, 211, 238, 0.75));
  box-shadow: 0 0 20px rgba(61, 125, 255, 0.5);
}
.msg.user .msg-avatar {
  color: var(--vdm-text-dim);
  background: rgba(122, 162, 255, 0.12);
  border: 1px solid var(--vdm-border);
}
.msg-body {
  flex: 1;
  min-width: 0;
}
.msg.user .msg-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.bubble-user {
  display: inline-block;
  max-width: 620px;
  padding: 11px 16px;
  border-radius: 16px 16px 4px 16px;
  color: #fff;
  font-size: 14.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  background: linear-gradient(135deg, rgba(61, 125, 255, 0.95), rgba(99, 102, 241, 0.9));
  box-shadow: 0 12px 30px rgba(61, 125, 255, 0.3);
}

.msg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
}
.msg-head .who {
  font-size: 13px;
  font-weight: 600;
  color: var(--vdm-text);
}
.meta-dot {
  font-size: 11px;
  color: var(--vdm-text-mute);
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(122, 162, 255, 0.08);
}
.meta-tag {
  font-size: 11px;
  color: var(--vdm-text-dim);
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--vdm-border);
}

.badge-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  padding: 3px 9px;
  border-radius: 999px;
}
.badge.ok {
  color: #7ff0c8;
  background: rgba(47, 211, 159, 0.14);
  border: 1px solid rgba(47, 211, 159, 0.3);
}
.badge.warn {
  color: #ffd694;
  background: rgba(240, 178, 74, 0.14);
  border: 1px solid rgba(240, 178, 74, 0.32);
}
.badge-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  padding: 3px 9px;
  border-radius: 999px;
  color: #a9c6ff;
  background: rgba(61, 125, 255, 0.1);
  border: 1px solid rgba(61, 125, 255, 0.28);
  cursor: pointer;
  transition: all 0.16s ease;
}
.badge-link:hover {
  background: rgba(61, 125, 255, 0.22);
  color: #fff;
}

/* 思考过程 */
.reasoning {
  margin-bottom: 10px;
  border-radius: 12px;
  border: 1px dashed var(--vdm-border-strong);
  background: rgba(122, 162, 255, 0.04);
  overflow: hidden;
}
.reasoning-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 13px;
  font-size: 12.5px;
  color: var(--vdm-text-dim);
  cursor: pointer;
  user-select: none;
  transition: background 0.16s ease;
}
.reasoning-head:hover {
  background: rgba(122, 162, 255, 0.06);
}
.reasoning-icon {
  color: var(--vdm-accent-2);
}
.reasoning-toggle {
  margin-left: auto;
  color: var(--vdm-text-mute);
}
.reasoning-body {
  padding: 10px 15px 13px;
  font-size: 13px;
  color: var(--vdm-text-dim);
  line-height: 1.7;
  max-height: 380px;
  overflow-y: auto;
  border-top: 1px dashed var(--vdm-border);
}

/* 答案面板 */
.answer-panel {
  border-radius: 16px;
  padding: 14px 18px;
  background: var(--vdm-panel);
  border: 1px solid var(--vdm-border);
  backdrop-filter: blur(12px);
  position: relative;
  overflow: hidden;
}
.answer-panel::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, #3d7dff, #22d3ee);
}
.answer-panel.is-warn::before {
  background: linear-gradient(180deg, #f0b24a, #ff9b6b);
}
.answer-panel.is-cmd::before {
  background: linear-gradient(180deg, #8a97b4, #5f6d8c);
}
.answer-head {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 9px;
  padding-bottom: 9px;
  border-bottom: 1px solid var(--vdm-border);
  font-size: 12.5px;
  font-weight: 600;
  color: var(--vdm-text-dim);
}
.answer-head .el-icon {
  color: var(--vdm-accent-2);
}
.answer-body {
  font-size: 14.5px;
  line-height: 1.8;
  color: var(--vdm-text);
  word-break: break-word;
}

/* 流式输出的打字光标 */
.answer-body :deep(.typing-caret) {
  display: inline-block;
  width: 9px;
  height: 1.05em;
  margin-left: 3px;
  vertical-align: text-bottom;
  border-radius: 2px;
  background: linear-gradient(180deg, #3d7dff, #22d3ee);
  box-shadow: 0 0 10px rgba(61, 125, 255, 0.85);
  animation: vdm-caret 1s steps(1, end) infinite;
}

.typing {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 8px 2px;
}
.typing-stage {
  margin-left: 6px;
  font-size: 12.5px;
  color: var(--vdm-text-mute);
}
.typing .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--vdm-accent-2);
  animation: vdm-blink 1.4s infinite both;
}
.typing .dot:nth-child(2) {
  animation-delay: 0.2s;
}
.typing .dot:nth-child(3) {
  animation-delay: 0.4s;
}

/* 非知识库警示 */
.warn-panel {
  display: flex;
  gap: 11px;
  margin-top: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(240, 178, 74, 0.08);
  border: 1px solid rgba(240, 178, 74, 0.28);
}
.warn-icon {
  color: #f0b24a;
  flex-shrink: 0;
  margin-top: 2px;
}
.warn-panel strong {
  display: block;
  font-size: 13px;
  color: #ffd694;
  margin-bottom: 3px;
}
.warn-panel p {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--vdm-text-dim);
}

/* 操作栏 */
.acts {
  display: flex;
  gap: 6px;
  margin-top: 10px;
}
.act {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 9px;
  font-size: 12.5px;
  color: var(--vdm-text-dim);
  background: rgba(122, 162, 255, 0.06);
  border: 1px solid var(--vdm-border);
  cursor: pointer;
  transition: all 0.16s ease;
}
.act:hover {
  color: #fff;
  border-color: var(--vdm-border-strong);
  background: rgba(61, 125, 255, 0.16);
}

/* 引用 */
.refs {
  margin-top: 12px;
  border-radius: 12px;
  border: 1px solid var(--vdm-border);
  background: rgba(122, 162, 255, 0.03);
  overflow: hidden;
}
.refs-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  font-size: 12.5px;
  color: var(--vdm-text-dim);
  cursor: pointer;
  user-select: none;
  transition: background 0.16s ease;
}
.refs-head:hover {
  background: rgba(122, 162, 255, 0.06);
}
.refs-toggle {
  margin-left: auto;
  color: var(--vdm-text-mute);
}
.refs-body {
  padding: 4px 10px 10px;
}

.doc-group {
  border: 1px solid var(--vdm-border);
  border-radius: 10px;
  background: rgba(10, 14, 26, 0.5);
  margin-bottom: 8px;
  overflow: hidden;
}
.doc-group:last-child {
  margin-bottom: 0;
}
.doc-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  padding: 9px 12px;
  font-size: 12.5px;
  cursor: pointer;
  user-select: none;
  transition: background 0.16s ease;
}
.doc-head:hover {
  background: rgba(122, 162, 255, 0.06);
}
.doc-head .el-icon {
  color: var(--vdm-accent);
}
.doc-name {
  color: var(--vdm-text);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-score {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--vdm-text-mute);
}
.doc-score b {
  margin-left: 4px;
}
.mini-tag {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 6px;
  color: var(--vdm-text-dim);
  background: rgba(122, 162, 255, 0.1);
  border: 1px solid var(--vdm-border);
}
.mini-tag.accent {
  color: #a9c6ff;
  background: rgba(61, 125, 255, 0.14);
  border-color: rgba(61, 125, 255, 0.28);
}
.doc-body {
  padding: 4px 10px 10px;
}
.chunk {
  padding: 9px 11px;
  margin-top: 7px;
  border-radius: 8px;
  background: rgba(122, 162, 255, 0.04);
  border: 1px dashed var(--vdm-border);
}
.chunk-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.chunk-score {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
}
.chunk-text {
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--vdm-text-dim);
  white-space: pre-wrap;
  max-height: 140px;
  overflow-y: auto;
}

/* ==================== 输入区 ==================== */
.composer {
  flex-shrink: 0;
  padding: 6px 26px 20px;
}
.composer-inner {
  max-width: 940px;
  margin: 0 auto;
}
.composer-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 9px;
}
.tool {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 11px;
  border-radius: 9px;
  font-size: 12.5px;
  color: var(--vdm-text-dim);
  background: transparent;
  border: 1px solid var(--vdm-border);
  cursor: pointer;
  transition: all 0.16s ease;
}
.tool:hover {
  color: #fff;
  background: rgba(122, 162, 255, 0.1);
}
.tool.on {
  color: #cfe0ff;
  border-color: rgba(61, 125, 255, 0.45);
  background: rgba(61, 125, 255, 0.16);
}
.composer-spacer {
  flex: 1;
}
.counter {
  font-size: 11.5px;
  color: var(--vdm-text-mute);
  font-family: var(--vdm-mono);
}

.settings {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding: 13px 16px;
  margin-bottom: 10px;
  border-radius: 12px;
  background: rgba(122, 162, 255, 0.05);
  border: 1px solid var(--vdm-border);
}
.set-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--vdm-text-dim);
}
.set-item.switch {
  flex-direction: row;
  align-items: center;
  gap: 10px;
  padding-top: 18px;
}

.composer-box {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 12px 12px 18px;
  border-radius: 20px;
  background: rgba(16, 22, 38, 0.85);
  border: 1px solid var(--vdm-border-strong);
  backdrop-filter: blur(20px);
  box-shadow: 0 20px 54px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.04);
  transition: all 0.2s ease;
}
.composer-box:focus-within {
  border-color: rgba(61, 125, 255, 0.65);
  box-shadow: 0 20px 60px rgba(61, 125, 255, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.06);
}
.composer-box :deep(.el-textarea__inner) {
  background: transparent;
  box-shadow: none;
  border: none;
  padding: 6px 0;
  color: var(--vdm-text);
  font-size: 14.5px;
  line-height: 1.75;
  font-family: var(--vdm-font);
}
.composer-box :deep(.el-textarea__inner)::placeholder {
  color: var(--vdm-text-mute);
}
.composer-send {
  flex-shrink: 0;
  width: 42px;
  height: 42px;
  border-radius: 14px;
  border: none;
  display: grid;
  place-items: center;
  color: #fff;
  cursor: pointer;
  background: linear-gradient(135deg, #3d7dff, #22d3ee);
  box-shadow: 0 8px 22px rgba(61, 125, 255, 0.5);
  transition: all 0.18s ease;
}
.composer-send:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 12px 30px rgba(61, 125, 255, 0.65);
}
.composer-send:disabled {
  opacity: 0.35;
  cursor: not-allowed;
  box-shadow: none;
}
.composer-send.stop {
  background: linear-gradient(135deg, #ff6b6b, #ff9b6b);
  box-shadow: 0 8px 22px rgba(255, 107, 107, 0.45);
}
.composer-hint {
  margin: 9px 0 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--vdm-text-mute);
}

/* ==================== 知识库选择弹窗 ==================== */
.kb-list {
  max-height: 400px;
  overflow-y: auto;
}
.kb-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 11px;
  cursor: pointer;
  margin-bottom: 6px;
  border: 1px solid transparent;
  transition: all 0.16s ease;
}
.kb-item:hover {
  background: rgba(122, 162, 255, 0.08);
}
.kb-item.active {
  background: rgba(61, 125, 255, 0.16);
  border-color: rgba(61, 125, 255, 0.45);
}
.kb-icon {
  color: var(--vdm-accent);
  flex-shrink: 0;
}
.kb-info {
  flex: 1;
  min-width: 0;
}
.kb-name {
  font-size: 14px;
  color: var(--vdm-text);
  margin-bottom: 2px;
}
.kb-desc {
  font-size: 11.5px;
  color: var(--vdm-text-mute);
}
.check-icon {
  color: #2fd39f;
  flex-shrink: 0;
}
.kb-empty {
  text-align: center;
  padding: 32px 0;
  color: var(--vdm-text-mute);
  font-size: 13px;
}

/* ==================== Markdown ==================== */
.markdown-content :deep(p) {
  margin: 7px 0;
}
.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  margin: 14px 0 8px;
  font-weight: 600;
  color: var(--vdm-text);
}
.markdown-content :deep(h1) {
  font-size: 19px;
}
.markdown-content :deep(h2) {
  font-size: 17px;
}
.markdown-content :deep(h3) {
  font-size: 15px;
}
.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  padding-left: 22px;
  margin: 8px 0;
}
.markdown-content :deep(li) {
  margin: 3px 0;
}
.markdown-content :deep(code) {
  font-family: var(--vdm-mono);
  font-size: 12.5px;
  padding: 2px 6px;
  border-radius: 6px;
  color: #ffb4a2;
  background: rgba(122, 162, 255, 0.12);
}
.markdown-content :deep(pre) {
  margin: 10px 0;
  padding: 14px 16px;
  border-radius: 12px;
  overflow-x: auto;
  background: rgba(4, 6, 13, 0.85);
  border: 1px solid var(--vdm-border);
}
.markdown-content :deep(pre code) {
  background: transparent;
  color: #d9e2f5;
  padding: 0;
  font-size: 12.5px;
  line-height: 1.7;
}
.markdown-content :deep(blockquote) {
  margin: 10px 0;
  padding: 8px 14px;
  border-left: 3px solid var(--vdm-accent);
  border-radius: 0 8px 8px 0;
  color: var(--vdm-text-dim);
  background: rgba(61, 125, 255, 0.08);
}
.markdown-content :deep(table) {
  width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  font-size: 13px;
}
.markdown-content :deep(th),
.markdown-content :deep(td) {
  padding: 7px 11px;
  border: 1px solid var(--vdm-border);
  text-align: left;
}
.markdown-content :deep(th) {
  background: rgba(122, 162, 255, 0.08);
  color: var(--vdm-text);
  font-weight: 600;
}
.markdown-content :deep(a) {
  color: #8fb6ff;
  text-decoration: none;
  border-bottom: 1px dashed rgba(143, 182, 255, 0.5);
}
.markdown-content :deep(a:hover) {
  color: #fff;
}
.markdown-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--vdm-border);
  margin: 14px 0;
}
.markdown-content :deep(img) {
  max-width: 100%;
  border-radius: 10px;
}

.mb-3 {
  margin-bottom: 12px;
}
</style>
