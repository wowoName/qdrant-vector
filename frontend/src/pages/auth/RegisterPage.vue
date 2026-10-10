<template>
  <div class="auth-page">
    <div class="vdm-backdrop"></div>
    <div class="auth-card">
      <div class="auth-logo">
        <div class="auth-mark">
          <svg viewBox="0 0 48 48" width="28" height="28" aria-hidden="true">
            <path
              d="M24 4 41 14v20L24 44 7 34V14z"
              fill="none"
              stroke="#ffffff"
              stroke-width="2.2"
              stroke-linejoin="round"
              opacity="0.92"
            />
            <circle cx="24" cy="24" r="5.2" fill="#ffffff" />
            <circle cx="24" cy="24" r="10.4" fill="none" stroke="rgba(255,255,255,.6)" stroke-width="1.3" />
          </svg>
        </div>
        <h1>创建账号</h1>
        <p>使用邮箱验证码注册 {{ APP_NAME }}</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" size="large">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" :prefix-icon="Message" clearable />
        </el-form-item>

        <el-form-item label="邮箱验证码" prop="code">
          <div class="code-row">
            <el-input
              v-model="form.code"
              placeholder="6 位验证码"
              maxlength="10"
              @keyup.enter="handleRegister"
            />
            <el-button
              :disabled="countdown > 0 || !form.email"
              :loading="sending"
              @click="handleSendCode"
            >
              {{ countdown > 0 ? `${countdown}s 后重发` : '获取验证码' }}
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="昵称" prop="nickname">
          <el-input v-model="form.nickname" placeholder="选填，默认取邮箱前缀" maxlength="100" />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 位"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="再次输入密码"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" class="submit-btn" :loading="loading" @click="handleRegister">
            注 册
          </el-button>
        </el-form-item>
      </el-form>

      <div class="auth-footer">
        已有账号？
        <router-link to="/login">直接登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, FormInstance, FormRules } from 'element-plus'
import { Message, Lock } from '@element-plus/icons-vue'
import { register, sendCode } from '@/api/auth'
import { setAuth } from '@/utils/user'
import { APP_NAME } from '@/config/app'

const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const sending = ref(false)
const countdown = ref(0)
let timer: ReturnType<typeof setInterval> | null = null

const form = reactive({
  email: '',
  code: '',
  nickname: '',
  password: '',
  confirmPassword: '',
})

const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  code: [{ required: true, message: '请输入邮箱验证码', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度 6~64 位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        if (value !== form.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

function startCountdown(seconds: number) {
  countdown.value = seconds
  timer = setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  }, 1000)
}

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function handleSendCode() {
  if (!form.email) {
    ElMessage.warning('请先输入邮箱')
    return
  }
  sending.value = true
  try {
    const res = await sendCode(form.email.trim())
    if (res.dev_code) {
      // 开发模式（未配置 SMTP）：直接提示验证码
      ElMessage.success(`开发模式验证码：${res.dev_code}`)
    } else {
      ElMessage.success(res.message || '验证码已发送，请查收邮箱')
    }
    startCountdown(60)
  } catch {
    // 错误信息由拦截器统一提示
  } finally {
    sending.value = false
  }
}

async function handleRegister() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const res = await register({
      email: form.email.trim(),
      code: form.code.trim(),
      password: form.password,
      nickname: form.nickname.trim() || undefined,
    })
    setAuth(res.token, res.user)
    ElMessage.success('注册成功')
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
  padding: 24px 0;
}

.auth-card {
  position: relative;
  z-index: 2;
  width: 440px;
  padding: 36px 38px 26px;
  border-radius: 22px;
  background: rgba(14, 20, 36, 0.78);
  border: 1px solid var(--vdm-border-strong);
  backdrop-filter: blur(24px);
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.6), var(--vdm-glow);
  animation: vdm-rise 0.5s ease both;
}

.auth-logo {
  text-align: center;
  margin-bottom: 22px;
}
.auth-mark {
  width: 54px;
  height: 54px;
  margin: 0 auto 13px;
  border-radius: 17px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #3d7dff, #22d3ee);
  box-shadow: 0 0 40px rgba(61, 125, 255, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.35);
}
.auth-logo h1 {
  margin: 0 0 6px;
  font-family: var(--vdm-font-display);
  font-size: 19px;
  font-weight: 800;
  letter-spacing: 3px;
  background: linear-gradient(100deg, #ffffff, #a9c6ff 50%, #22d3ee);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.auth-logo p {
  margin: 0;
  font-size: 12.5px;
  color: var(--vdm-text-mute);
}

.code-row {
  display: flex;
  gap: 10px;
  width: 100%;
}
.code-row .el-input {
  flex: 1;
}

.submit-btn {
  width: 100%;
  height: 46px;
  font-size: 15px;
  letter-spacing: 4px;
  border: none;
  background: linear-gradient(135deg, #3d7dff, #22d3ee);
  box-shadow: 0 12px 30px rgba(61, 125, 255, 0.45);
}
.submit-btn:hover {
  box-shadow: 0 16px 40px rgba(61, 125, 255, 0.6);
}

.auth-footer {
  text-align: center;
  font-size: 13px;
  color: var(--vdm-text-mute);
}
.auth-footer a {
  color: #8fb6ff;
  text-decoration: none;
}
.auth-footer a:hover {
  color: #fff;
}
</style>
