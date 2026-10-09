<template>
  <el-container class="layout-container">
    <!-- 侧边栏 -->
    <el-aside width="220px" class="sidebar">
      <div class="logo">
        <el-icon :size="24" color="#409eff"><Collection /></el-icon>
        <span class="logo-text">Vector DM</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="#001529"
        text-color="#fff"
        active-text-color="#409eff"
      >
        <el-menu-item index="/collections">
          <el-icon><Collection /></el-icon>
          <span>知识库</span>
        </el-menu-item>
        <el-menu-item index="/search">
          <el-icon><Search /></el-icon>
          <span>向量检索</span>
        </el-menu-item>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>chat</span>
        </el-menu-item>
        <el-menu-item index="/tasks">
          <el-icon><List /></el-icon>
          <span>任务中心</span>
        </el-menu-item>
        <el-menu-item v-if="isAdminUser" index="/admin/users">
          <el-icon><User /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主内容区 -->
    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ pageTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-tag size="small" type="info" class="role-tag">{{ roleLabel }}</el-tag>
          <el-dropdown trigger="click" @command="handleCommand">
            <span class="user-entry">
              <el-avatar :size="28" class="avatar">{{ avatarText }}</el-avatar>
              <span class="nickname">{{ user?.nickname || user?.email || '未登录' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  {{ user?.email }}
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
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>

    <!-- 个人资料弹窗（修改名称 / 密码） -->
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
  </el-container>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import {
  ArrowDown,
  ChatDotRound,
  Collection,
  EditPen,
  List,
  Search,
  SwitchButton,
  User,
} from '@element-plus/icons-vue'
import { clearAuth, getUser, isAdmin as isAdminFn, roleLabel as getRoleLabel, setUser } from '@/utils/user'
import { updateMe } from '@/api/auth'

const route = useRoute()
const router = useRouter()

const user = ref(getUser())
const isAdminUser = computed(() => isAdminFn())
const roleLabel = computed(() => getRoleLabel())
const avatarText = computed(() => {
  const name = user.value?.nickname || user.value?.email || '?'
  return name.slice(0, 1).toUpperCase()
})

const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/collections')) return '/collections'
  return path
})

const pageTitle = computed(() => route.meta?.title as string || '')

// ---------- 个人资料（修改名称/密码） ----------
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
        if (value && value.length < 6) {
          callback(new Error('新密码至少 6 位'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
  old_password: [
    {
      validator: (_rule, value, callback) => {
        // 填了新密码才要求二次校验当前密码
        if (profile.form.password && !value) {
          callback(new Error('请输入当前密码'))
        } else {
          callback()
        }
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

function handleCommand(command: string) {
  if (command === 'logout') {
    clearAuth()
    router.push('/login')
  } else if (command === 'profile') {
    openProfile()
  }
}
</script>

<style scoped>
.layout-container {
  height: 100vh;
}

.sidebar {
  background-color: #001529;
  color: #fff;
  overflow: hidden;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-bottom: 1px solid #1f3a5f;
}

.logo-text {
  font-size: 18px;
  font-weight: bold;
  color: #fff;
}

.header {
  background: #fff;
  border-bottom: 1px solid #e6e6e6;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.main-content {
  background: #f5f7fa;
  padding: 20px;
  overflow-y: auto;
}

.header-left {
  flex: 1;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.role-tag {
  margin-right: 4px;
}

.user-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  outline: none;
}

.avatar {
  background: #409eff;
  color: #fff;
  font-size: 13px;
}

.nickname {
  font-size: 14px;
  color: #1f2d3d;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.el-menu) {
  border-right: none;
}
</style>
