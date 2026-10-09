<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-logo">
        <el-icon :size="36" color="#409eff"><Collection /></el-icon>
        <h1>Vector Data Manager</h1>
        <p>生产级向量数据库管理平台</p>
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
import { Collection, Message, Lock } from '@element-plus/icons-vue'
import { login } from '@/api/auth'
import { setAuth } from '@/utils/user'

const router = useRouter()
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
    setAuth(res.token, res.user)
    ElMessage.success('登录成功')
    router.push('/collections')
  } catch {
    // 错误信息由拦截器统一提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #e8f1fd 0%, #f5f7fa 100%);
}

.auth-card {
  width: 400px;
  padding: 40px 36px 28px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0, 30, 80, 0.08);
}

.auth-logo {
  text-align: center;
  margin-bottom: 24px;
}

.auth-logo h1 {
  font-size: 20px;
  margin: 10px 0 4px;
  color: #1f2d3d;
}

.auth-logo p {
  font-size: 13px;
  color: #909399;
  margin: 0;
}

.submit-btn {
  width: 100%;
}

.auth-footer {
  text-align: center;
  font-size: 13px;
  color: #909399;
}

.auth-footer a {
  color: #409eff;
  text-decoration: none;
}
</style>
