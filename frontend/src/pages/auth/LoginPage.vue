<template>
  <div class="auth-page">
    <div class="vdm-backdrop"></div>
    <div class="auth-card">
      <div class="auth-logo">
        <div class="auth-mark">
          <img :src="logoUrl" alt="系统 Logo" />
        </div>
        <h1>{{ APP_NAME }}</h1>
        <p>向量数据中枢 · 生产级向量数据库管理平台</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" size="large">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" :prefix-icon="Message" clearable />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" class="submit-btn" :loading="loading" @click="handleLogin">
            登 录
          </el-button>
        </el-form-item>
      </el-form>

      <div class="auth-footer">
        还没有账号？
        <router-link to="/register">立即注册</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, FormInstance, FormRules } from 'element-plus'
import { Message, Lock } from '@element-plus/icons-vue'
import { login } from '@/api/auth'
import logoUrl from '@/assets/images/logo.jpg'
import { setAuth } from '@/utils/user'
import { useChatStore } from '@/stores/chat'
import { APP_NAME } from '@/config/app'

const router = useRouter()
const chat = useChatStore()
const formRef = ref<FormInstance>()
const loading = ref(false)

const form = reactive({
  email: '',
  password: '123456',
})

const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const res = await login({ email: form.email.trim(), password: form.password })
    // 登录前先清空上一位用户残留的会话/知识库数据，避免侧边栏短暂显示他人记录
    chat.clearUserData()
    setAuth(res.token, res.user)
    ElMessage.success('登录成功')
    router.push('/chat')
  } catch {
    // 错误信息由拦截器统一提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--vdm-void);
  overflow: hidden;
}

.auth-card {
  position: relative;
  z-index: 2;
  width: 420px;
  padding: 40px 38px 30px;
  border-radius: 22px;
  background: #ffffff;
  border: 1px solid var(--vdm-border);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.1);
  animation: vdm-rise 0.5s ease both;
}

.auth-logo {
  text-align: center;
  margin-bottom: 26px;
}
.auth-mark {
  width: 58px;
  height: 58px;
  margin: 0 auto 14px;
  border-radius: 18px;
  overflow: hidden;
  display: grid;
  place-items: center;
  box-shadow: 0 10px 26px rgba(37, 99, 235, 0.3);
}
.auth-mark img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.auth-logo h1 {
  margin: 0 0 6px;
  font-family: var(--vdm-font-display);
  font-size: 24px;
  font-weight: 800;
  letter-spacing: 10px;
  text-indent: 10px;
  color: var(--vdm-text);
}
.auth-logo p {
  margin: 0;
  font-size: 12.5px;
  color: var(--vdm-text-mute);
}

.submit-btn {
  width: 100%;
  height: 46px;
  font-size: 15px;
  letter-spacing: 4px;
  border: none;
  background: linear-gradient(135deg, #2563eb, #3b82f6);
  box-shadow: 0 12px 26px rgba(37, 99, 235, 0.28);
}
.submit-btn:hover {
  box-shadow: 0 16px 34px rgba(37, 99, 235, 0.38);
}

.auth-footer {
  text-align: center;
  font-size: 13px;
  color: var(--vdm-text-mute);
}
.auth-footer a {
  color: var(--vdm-accent);
  text-decoration: none;
}
.auth-footer a:hover {
  color: #1d4fd0;
}
</style>
