<template>
  <div class="app-shell" :class="{ collapsed }">
    <!-- 氛围背景 -->
    <div class="vdm-backdrop"></div>

    <!-- ============ 左侧栏 ============ -->
    <aside class="sidebar">
      <!-- 折叠控制 -->
      <button class="collapse-handle" @click="collapsed = !collapsed">
        <el-icon>
          <component :is="collapsed ? 'Expand' : 'Fold'" />
        </el-icon>
      </button>

      <!-- 品牌 -->
      <div class="brand">
        <div class="brand-mark">
          <svg viewBox="0 0 48 48" width="22" height="22" aria-hidden="true">
            <defs>
              <linearGradient id="vdmMarkG" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#ffffff" />
                <stop offset="100%" stop-color="#cfe0ff" />
              </linearGradient>
            </defs>
            <path
              d="M24 4 41 14v20L24 44 7 34V14z"
              fill="none"
              stroke="url(#vdmMarkG)"
              stroke-width="2.4"
              stroke-linejoin="round"
            />
            <circle cx="24" cy="24" r="5.4" fill="url(#vdmMarkG)" />
            <circle cx="24" cy="24" r="10.5" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="1.4" />
          </svg>
        </div>
        <div class="brand-text">
          <span class="brand-name">新页</span>
          <span class="brand-sub">向量数据中枢</span>
        </div>
      </div>

      <!-- 新对话 -->
      <button class="new-chat" @click="startNewChat">
        <el-icon :size="17"><Plus /></el-icon>
        <span>开启新对话</span>
      </button>

      <!-- 历史对话 -->
      <div class="conv-wrap">
        <div class="conv-search">
          <el-input
            v-model="keyword"
            placeholder="搜索对话"
            :prefix-icon="Search"
            clearable
            size="default"
          />
        </div>

        <div class="conv-list">
          <div class="conv-section-label" v-if="filteredConversations.length">最近对话</div>
          <div
            v-for="conv in filteredConversations"
            :key="conv.session_id"
            class="conv-item"
            :class="{ active: isActiveConv(conv) }"
            @click="openConversation(conv)"
          >
            <el-icon class="conv-icon" :size="15"><ChatDotRound /></el-icon>
            <div class="conv-main">
              <div class="conv-title">{{ conv.title || '新对话' }}</div>
              <div class="conv-meta">
                <span>{{ conv.collection_id ? chat.getCollectionName(conv.collection_id) : '全知识库' }}</span>
                <span class="sep">·</span>
                <span>{{ formatTime(conv.updated_at) }}</span>
              </div>
            </div>
            <el-dropdown trigger="click" @click.stop>
              <button class="conv-more" @click.stop>
                <el-icon :size="14"><MoreFilled /></el-icon>
              </button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="handleDeleteConversation(conv)">
                    <el-icon><Delete /></el-icon>删除对话
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>

          <div v-if="!filteredConversations.length" class="conv-empty">
            <el-icon :size="26"><ChatDotRound /></el-icon>
            <p>{{ keyword ? '未找到匹配的对话' : '还没有对话记录' }}</p>
          </div>
        </div>
      </div>

      <!-- 左下角：用户区 -->
      <div class="sidebar-footer">
        <div class="sys-status">
          <span class="status-dot"></span>
          <span>系统在线</span>
          <span class="sys-ver">v0.1</span>
        </div>

        <el-dropdown
          trigger="click"
          placement="top-start"
          popper-class="vdm-user-menu"
          @command="handleCommand"
        >
          <div class="user-chip">
            <div class="avatar">
              <span>{{ avatarText }}</span>
            </div>
            <div class="user-info">
              <span class="user-name">{{ user?.nickname || user?.email || '未登录' }}</span>
              <span class="user-role">{{ roleLabel }}</span>
            </div>
            <el-icon class="chev" :size="15"><ArrowUp /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="collections">
                <el-icon><Collection /></el-icon>知识库管理
              </el-dropdown-item>
              <el-dropdown-item command="search">
                <el-icon><Search /></el-icon>向量检索
              </el-dropdown-item>
              <el-dropdown-item command="tasks">
                <el-icon><List /></el-icon>任务中心
              </el-dropdown-item>
              <el-dropdown-item v-if="isAdminUser" command="users">
                <el-icon><User /></el-icon>用户管理
              </el-dropdown-item>
              <el-dropdown-item divided command="profile">
                <el-icon><EditPen /></el-icon>个人资料
              </el-dropdown-item>
              <el-dropdown-item command="logout">
                <el-icon><SwitchButton /></el-icon>退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </aside>

    <!-- ============ 主区域 ============ -->
    <main class="main-area">
      <!-- 非对话页顶部栏 -->
      <header v-if="!isImmersive" class="topbar">
        <button class="back-btn" @click="goChat">
          <el-icon :size="15"><ArrowLeft /></el-icon>
          <span>返回对话</span>
        </button>
        <div class="topbar-divider"></div>
        <div class="topbar-title">
          <h1>{{ pageTitle }}</h1>
          <p v-if="pageSubtitle">{{ pageSubtitle }}</p>
        </div>
      </header>

      <div class="content" :class="{ immersive: isImmersive }">
        <router-view />
      </div>
    </main>

    <!-- ============ 个人资料弹窗 ============ -->
    <el-dialog v-model="profile.visible" title="个人资料" width="440px" destroy-on-close>
      <el-form ref="profileFormRef" :model="profile.form" :rules="profileRules" label-width="80px">
        <el-form-item label="邮箱">
          <el-input :model-value="user?.email || ''" disabled />
        </el-form-item>
        <el-form-item label="名称" prop="nickname">
          <el-input v-model="profile.form.nickname" maxlength="100" placeholder="请输入名称" />
        </el-form-item>
        <el-form-item label="当前密码" prop="old_password">
          <el-input
            v-model="profile.form.old_password"
            type="password"
            show-password
            placeholder="修改密码时必填"
          />
        </el-form-item>
        <el-form-item label="新密码" prop="password">
          <el-input
            v-model="profile.form.password"
            type="password"
            show-password
            placeholder="留空则不修改密码"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="profile.visible = false">取消</el-button>
        <el-button type="primary" :loading="profile.saving" @click="handleSaveProfile">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import {
  Plus,
  Search,
  ChatDotRound,
  MoreFilled,
  Delete,
  Collection,
  List,
  User,
  EditPen,
  SwitchButton,
  ArrowLeft,
  ArrowUp,
} from '@element-plus/icons-vue'
import { deleteConversation, type Conversation } from '@/api/chat'
import { clearAuth, getUser, isAdmin as isAdminFn, roleLabel as getRoleLabel, setUser } from '@/utils/user'
import { updateMe } from '@/api/auth'
import { useChatStore } from '@/stores/chat'

const route = useRoute()
const router = useRouter()
const chat = useChatStore()

const collapsed = ref(false)
const keyword = ref('')

const user = ref(getUser())
const isAdminUser = computed(() => isAdminFn())
const roleLabel = computed(() => getRoleLabel())
const avatarText = computed(() => {
  const name = user.value?.nickname || user.value?.email || '?'
  return name.slice(0, 1).toUpperCase()
})

const isImmersive = computed(() => route.name === 'Chat')
const pageTitle = computed(() => (route.meta?.title as string) || '')
const pageSubtitle = computed(() => (route.meta?.subtitle as string) || '')

const filteredConversations = computed(() => {
  const list = chat.conversations
  if (!keyword.value) return list
  const kw = keyword.value.toLowerCase()
  return list.filter((c) => (c.title || '').toLowerCase().includes(kw))
})

function isActiveConv(conv: Conversation) {
  return route.name === 'Chat' && chat.sessionId === conv.session_id
}

function formatTime(dateStr: string): string {
  const date = new Date(dateStr)
  const diff = Date.now() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  return `${date.getMonth() + 1}/${date.getDate()}`
}

function goChat() {
  router.push('/chat')
}

function startNewChat() {
  chat.reset()
  if (route.name === 'Chat') {
    router.replace({ path: '/chat' })
  } else {
    router.push('/chat')
  }
}

async function openConversation(conv: Conversation) {
  const sid = conv.session_id
  if (route.name === 'Chat' && chat.sessionId === sid) return
  if (chat.isSending) {
    ElMessage.warning('请等待当前对话完成')
    return
  }
  await chat.loadConversation(sid)
  if (route.name === 'Chat') {
    router.replace({ path: '/chat', query: { session: sid } })
  } else {
    router.push({ path: '/chat', query: { session: sid } })
  }
}

async function handleDeleteConversation(conv: Conversation) {
  try {
    await ElMessageBox.confirm('确定删除这个对话吗？', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await deleteConversation(conv.session_id)
    chat.removeConversationLocal(conv.session_id)
    if (chat.sessionId === conv.session_id) {
      startNewChat()
    }
    ElMessage.success('已删除')
  } catch {
    // 用户取消
  }
}

function handleCommand(command: string) {
  if (command === 'collections') router.push('/collections')
  else if (command === 'search') router.push('/search')
  else if (command === 'tasks') router.push('/tasks')
  else if (command === 'users') router.push('/admin/users')
  else if (command === 'profile') openProfile()
  else if (command === 'logout') {
    clearAuth()
    router.push('/login')
  }
}

// ---------- 个人资料 ----------
const profile = reactive({
  visible: false,
  saving: false,
  form: { nickname: '', old_password: '', password: '' },
})
const profileFormRef = ref<FormInstance>()
const profileRules: FormRules = {
  nickname: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  password: [
    {
      validator: (_rule, value, callback) => {
        if (value && value.length < 6) callback(new Error('新密码至少 6 位'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  old_password: [
    {
      validator: (_rule, value, callback) => {
        if (profile.form.password && !value) callback(new Error('请输入当前密码'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

function openProfile() {
  profile.form.nickname = user.value?.nickname || ''
  profile.form.old_password = ''
  profile.form.password = ''
  profile.visible = true
}

async function handleSaveProfile() {
  const valid = await profileFormRef.value?.validate().catch(() => false)
  if (!valid) return
  profile.saving = true
  try {
    const updated = await updateMe({
      nickname: profile.form.nickname.trim() || undefined,
      old_password: profile.form.password ? profile.form.old_password : undefined,
      password: profile.form.password || undefined,
    })
    setUser(updated)
    user.value = updated
    profile.visible = false
    ElMessage.success('已更新')
  } catch {
    // 错误信息由拦截器统一提示
  } finally {
    profile.saving = false
  }
}

onMounted(() => {
  chat.fetchConversations()
  chat.fetchCollections()
})
</script>

<style scoped>
.app-shell {
  position: relative;
  display: flex;
  height: 100vh;
  width: 100%;
  overflow: hidden;
  background: var(--vdm-void);
}

/* ==================== 侧边栏 ==================== */
.sidebar {
  position: relative;
  z-index: 2;
  width: 292px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, rgba(15, 21, 38, 0.92), rgba(8, 12, 22, 0.88));
  backdrop-filter: blur(26px);
  border-right: 1px solid var(--vdm-border);
  transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}

.app-shell.collapsed .sidebar {
  width: 80px;
}

.collapse-handle {
  position: absolute;
  top: 26px;
  right: -13px;
  z-index: 6;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid var(--vdm-border-strong);
  background: #0e1424;
  color: var(--vdm-text-dim);
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.5);
  transition: all 0.18s ease;
}
.collapse-handle:hover {
  color: #fff;
  border-color: var(--vdm-accent);
  box-shadow: 0 0 16px rgba(61, 125, 255, 0.6);
}

.brand {
  height: 72px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 20px;
  overflow: hidden;
}
.brand-mark {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border-radius: 12px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #3d7dff, #22d3ee);
  box-shadow: 0 0 22px rgba(61, 125, 255, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}
.brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  white-space: nowrap;
}
.brand-name {
  font-family: var(--vdm-font-display);
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 2px;
  background: linear-gradient(100deg, #ffffff, #a9c6ff 55%, #22d3ee);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.brand-sub {
  font-size: 10px;
  letter-spacing: 2.4px;
  color: var(--vdm-text-mute);
}

.new-chat {
  flex-shrink: 0;
  margin: 2px 16px 14px;
  height: 44px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 15px;
  border-radius: 12px;
  border: 1px solid rgba(122, 162, 255, 0.4);
  background: linear-gradient(135deg, rgba(61, 125, 255, 0.95), rgba(34, 211, 238, 0.78));
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  box-shadow: 0 10px 26px rgba(61, 125, 255, 0.3);
  transition: all 0.2s ease;
}
.new-chat:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 34px rgba(61, 125, 255, 0.45);
}
.new-chat:active {
  transform: translateY(0);
}

.conv-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.conv-search {
  padding: 0 16px 8px;
  flex-shrink: 0;
}
.conv-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 12px 12px;
}
.conv-section-label {
  font-size: 10.5px;
  letter-spacing: 1.6px;
  text-transform: uppercase;
  color: var(--vdm-text-mute);
  padding: 10px 8px 6px;
}

.conv-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 10px;
  border-radius: 11px;
  border: 1px solid transparent;
  cursor: pointer;
  color: var(--vdm-text-dim);
  margin-bottom: 3px;
  transition: all 0.16s ease;
}
.conv-item:hover {
  background: rgba(122, 162, 255, 0.07);
  color: var(--vdm-text);
}
.conv-item.active {
  background: linear-gradient(90deg, rgba(61, 125, 255, 0.24), rgba(34, 211, 238, 0.06));
  border-color: rgba(122, 162, 255, 0.32);
  color: #fff;
}
.conv-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 22px;
  border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, #3d7dff, #22d3ee);
  box-shadow: 0 0 12px rgba(61, 125, 255, 0.9);
}
.conv-icon {
  flex-shrink: 0;
  opacity: 0.85;
}
.conv-main {
  flex: 1;
  min-width: 0;
}
.conv-title {
  font-size: 13.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
}
.conv-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--vdm-text-mute);
  white-space: nowrap;
  overflow: hidden;
}
.conv-meta .sep {
  opacity: 0.6;
}
.conv-more {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--vdm-text-mute);
  cursor: pointer;
  opacity: 0;
  transition: all 0.16s ease;
}
.conv-item:hover .conv-more {
  opacity: 1;
}
.conv-more:hover {
  background: rgba(122, 162, 255, 0.16);
  color: #fff;
}
.conv-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 46px 16px;
  color: var(--vdm-text-mute);
  font-size: 12.5px;
}
.conv-empty p {
  margin: 0;
}

/* ==================== 左下角用户区 ==================== */
.sidebar-footer {
  flex-shrink: 0;
  margin-top: auto;
  padding: 12px 14px 14px;
  border-top: 1px solid var(--vdm-border);
  background: rgba(8, 12, 22, 0.5);
}
/* el-dropdown 默认为 inline-flex，会收缩到内容宽度导致无法居中/溢出 */
.sidebar-footer :deep(.el-dropdown) {
  display: block;
  width: 100%;
}
.sys-status {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 8px 9px;
  font-size: 11px;
  color: var(--vdm-text-mute);
  letter-spacing: 0.4px;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #2fd39f;
  box-shadow: 0 0 9px #2fd39f;
  animation: vdm-pulse 2.4s ease-in-out infinite;
}
.sys-ver {
  margin-left: auto;
  font-family: var(--vdm-mono);
  opacity: 0.7;
}
.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 12px;
  border: 1px solid transparent;
  cursor: pointer;
  outline: none;
  transition: all 0.16s ease;
}
.user-chip:hover {
  background: rgba(122, 162, 255, 0.09);
  border-color: var(--vdm-border);
}
.avatar {
  position: relative;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #4d6bfe, #8b5cf6);
  box-shadow: 0 0 18px rgba(77, 107, 254, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}
.avatar::after {
  content: '';
  position: absolute;
  right: -2px;
  bottom: -2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #2fd39f;
  border: 2px solid #0b1020;
}
.user-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow: hidden;
}
.user-name {
  font-size: 13.5px;
  color: var(--vdm-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.user-role {
  font-size: 11px;
  color: var(--vdm-text-mute);
}
.chev {
  color: var(--vdm-text-mute);
  flex-shrink: 0;
}

/* 折叠态 */
.app-shell.collapsed .brand {
  justify-content: center;
  padding: 0;
}
.app-shell.collapsed .brand-text,
.app-shell.collapsed .new-chat span,
.app-shell.collapsed .conv-wrap,
.app-shell.collapsed .user-info,
.app-shell.collapsed .chev,
.app-shell.collapsed .sys-status {
  display: none;
}
.app-shell.collapsed .new-chat {
  width: 44px;
  margin: 2px auto 14px;
  padding: 0;
  justify-content: center;
}
.app-shell.collapsed .sidebar-footer {
  padding: 12px 0 16px;
}
.app-shell.collapsed .user-chip {
  justify-content: center;
  padding: 8px 0;
}

/* ==================== 主区域 ==================== */
.main-area {
  position: relative;
  z-index: 2;
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  flex-shrink: 0;
  height: 70px;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 26px;
  border-bottom: 1px solid var(--vdm-border);
  background: rgba(9, 13, 24, 0.6);
  backdrop-filter: blur(14px);
}
.back-btn {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 13px;
  border-radius: 10px;
  border: 1px solid var(--vdm-border);
  background: rgba(122, 162, 255, 0.06);
  color: var(--vdm-text-dim);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.16s ease;
}
.back-btn:hover {
  color: #fff;
  border-color: var(--vdm-border-strong);
  background: rgba(61, 125, 255, 0.16);
}
.topbar-divider {
  width: 1px;
  height: 26px;
  background: var(--vdm-border);
}
.topbar-title h1 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 0.4px;
}
.topbar-title p {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--vdm-text-mute);
}

.content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 22px 26px 26px;
}
.content.immersive {
  overflow: hidden;
  padding: 0;
}
</style>
