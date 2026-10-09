<template>
  <div class="page-container">
    <div class="toolbar">
      <el-input
        v-model="query.keyword"
        placeholder="搜索邮箱 / 昵称"
        clearable
        style="width: 190px"
        @keyup.enter="loadData"
        @clear="loadData"
      />
      <el-input
        v-model="query.tenant"
        placeholder="租户名称"
        clearable
        style="width: 170px"
        @keyup.enter="loadData"
        @clear="loadData"
      />
      <el-select v-model="query.status" placeholder="状态" clearable style="width: 110px" @change="loadData">
        <el-option label="启用" value="active" />
        <el-option label="禁用" value="disabled" />
      </el-select>
      <el-date-picker
        v-model="query.createdRange"
        type="daterange"
        value-format="YYYY-MM-DD"
        range-separator="至"
        start-placeholder="注册起始"
        end-placeholder="注册结束"
        style="width: 240px"
        @change="loadData"
      />
      <el-date-picker
        v-model="query.expiresRange"
        type="daterange"
        value-format="YYYY-MM-DD"
        range-separator="至"
        start-placeholder="到期起始"
        end-placeholder="到期结束"
        style="width: 240px"
        @change="loadData"
      />
      <el-button type="primary" @click="loadData">查询</el-button>
      <el-button @click="resetQuery">重置</el-button>
      <div class="spacer" />
      <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建用户</el-button>
    </div>

    <el-table
      :data="rows"
      v-loading="loading"
      border
      stripe
      row-key="id"
      default-expand-all
      :tree-props="{ children: 'children' }"
    >
      <el-table-column prop="email" label="邮箱 / 租户" min-width="240">
        <template #default="{ row }">
          <span class="email">{{ row.email }}</span>
          <el-tag v-if="row.role !== 'user'" size="small" type="info" class="owner-tag">租户</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="nickname" label="昵称" min-width="120" />
      <el-table-column label="角色" width="110">
        <template #default="{ row }">
          <el-tag :type="roleTagType(row.role)" size="small">{{ roleText(row.role) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
            {{ row.status === 'active' ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="注册日期" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="过期日期" width="170">
        <template #default="{ row }">
          <!-- 租户查看子用户时不显示过期日期：租户过期则子用户自然过期 -->
          <span v-if="!isSuperAdminUser && row.role === 'user'" class="never">跟随租户</span>
          <template v-else>
            <span v-if="row.expires_at" :class="{ expired: isExpired(row.expires_at) }">
              {{ formatTime(row.expires_at) }}
            </span>
            <span v-else class="never">永不过期</span>
          </template>
        </template>
      </el-table-column>
      <el-table-column label="最近登录" width="170">
        <template #default="{ row }">{{ formatTime(row.last_login_at) || '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <template v-if="row.id !== currentUserId">
            <el-button link type="primary" size="small" @click="openEditDialog(row)">
              {{ isSuperAdminUser ? '编辑' : '查看' }}
            </el-button>
            <el-button
              link
              :type="row.status === 'active' ? 'warning' : 'success'"
              size="small"
              @click="toggleStatus(row)"
            >
              {{ row.status === 'active' ? '禁用' : '启用' }}
            </el-button>
            <el-popconfirm title="确定删除该用户？其所有数据归属将被解除" @confirm="handleDelete(row)">
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
          <template v-else>
            <el-button link type="primary" size="small" @click="openSelfDialog(row)">编辑</el-button>
            <span class="self-tip">当前账号</span>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑用户弹窗 -->
    <el-dialog v-model="dialog.visible" :title="dialogTitle" width="480px" destroy-on-close>
      <el-form ref="dialogFormRef" :model="dialog.form" :rules="dialogRules" label-width="90px">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="dialog.form.email" :disabled="dialog.mode !== 'create'" placeholder="user@example.com" />
        </el-form-item>
        <el-form-item v-if="dialog.mode !== 'create'" label="角色">
          <el-input :model-value="roleText(dialog.form.role)" disabled />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input
            v-model="dialog.form.nickname"
            maxlength="100"
            :disabled="dialog.mode === 'editOther'"
            :placeholder="dialog.mode === 'editOther' ? '不可修改' : ''"
          />
        </el-form-item>
        <el-form-item v-if="showPassword" label="密码" prop="password">
          <el-input
            v-model="dialog.form.password"
            type="password"
            show-password
            :placeholder="dialog.mode === 'create' ? '至少 6 位' : '留空则不修改密码'"
          />
        </el-form-item>
        <el-form-item v-if="showExpiry" label="过期日期">
          <el-date-picker
            v-model="dialog.form.expires_at"
            type="datetime"
            placeholder="选择过期时间"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 100%"
            clearable
          />
          <div class="form-tip">留空表示永不过期</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">{{ canSave ? '取消' : '关闭' }}</el-button>
        <el-button v-if="canSave" type="primary" :loading="dialog.saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, FormInstance, FormRules } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { createUser, deleteUser, getUserTree, updateUser, type UserTreeNode } from '@/api/admin'
import { updateMe } from '@/api/auth'
import { getUser, isSuperAdmin, setUser } from '@/utils/user'

const loading = ref(false)
const rows = ref<UserTreeNode[]>([])
const currentUserId = computed(() => getUser()?.id || '')
const isSuperAdminUser = computed(() => isSuperAdmin())

const query = reactive({
  keyword: '',
  tenant: '',
  status: '',
  createdRange: null as string[] | null,
  expiresRange: null as string[] | null,
})

type DialogMode = 'create' | 'editOther' | 'editSelf'

const dialog = reactive({
  visible: false,
  mode: 'create' as DialogMode,
  saving: false,
  userId: '',
  form: {
    email: '',
    role: '',
    nickname: '',
    password: '',
    expires_at: '',
  },
})

const dialogTitle = computed(() => {
  if (dialog.mode === 'create') return '新建用户'
  if (dialog.mode === 'editSelf') return '编辑个人信息'
  return isSuperAdminUser.value ? '编辑用户（设置过期时间）' : '查看用户'
})
const isCreate = computed(() => dialog.mode === 'create')
// 编辑自己：可改昵称/密码；编辑他人：昵称只读
const showPassword = computed(() => dialog.mode !== 'editOther')
// 过期日期只有总管理员可见/可设置：租户查看子用户时不显示（跟随租户过期）
const showExpiry = computed(() => isSuperAdminUser.value && dialog.mode !== 'editSelf')
// 租户编辑他人时无可改字段，仅作为只读查看
const canSave = computed(() => dialog.mode !== 'editOther' || isSuperAdminUser.value)

const dialogFormRef = ref<FormInstance>()

const dialogRules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    {
      validator: (_rule, value, callback) => {
        if (isCreate.value && (!value || value.length < 6)) {
          callback(new Error('密码至少 6 位'))
        } else if (value && value.length < 6) {
          callback(new Error('密码至少 6 位'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

function formatTime(value?: string | null): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : ''
}

function isExpired(value?: string | null): boolean {
  return !!value && dayjs(value).isBefore(dayjs())
}

function roleText(role: string): string {
  if (role === 'super_admin') return '总管理员'
  if (role === 'tenant_admin') return '租户管理员'
  return '普通用户'
}

function roleTagType(role: string): string {
  if (role === 'super_admin') return 'danger'
  if (role === 'tenant_admin') return 'warning'
  return 'info'
}

async function loadData() {
  loading.value = true
  try {
    const c = query.createdRange
    const e = query.expiresRange
    rows.value = await getUserTree({
      keyword: query.keyword || undefined,
      tenant: query.tenant || undefined,
      status: query.status || undefined,
      created_from: c && c[0] ? `${c[0]}T00:00:00` : undefined,
      created_to: c && c[1] ? `${c[1]}T23:59:59` : undefined,
      expires_from: e && e[0] ? `${e[0]}T00:00:00` : undefined,
      expires_to: e && e[1] ? `${e[1]}T23:59:59` : undefined,
    })
  } finally {
    loading.value = false
  }
}

function resetQuery() {
  query.keyword = ''
  query.tenant = ''
  query.status = ''
  query.createdRange = null
  query.expiresRange = null
  loadData()
}

function openCreateDialog() {
  dialog.mode = 'create'
  dialog.userId = ''
  dialog.form = { email: '', role: '', nickname: '', password: '', expires_at: '' }
  dialog.visible = true
}

function openEditDialog(row: UserTreeNode) {
  dialog.mode = 'editOther'
  dialog.userId = row.id
  dialog.form = {
    email: row.email,
    role: row.role,
    nickname: row.nickname || '',
    password: '',
    expires_at: row.expires_at ? dayjs(row.expires_at).format('YYYY-MM-DDTHH:mm:ss') : '',
  }
  dialog.visible = true
}

function openSelfDialog(row: UserTreeNode) {
  dialog.mode = 'editSelf'
  dialog.userId = row.id
  dialog.form = { email: row.email, role: row.role, nickname: row.nickname || '', password: '', expires_at: '' }
  dialog.visible = true
}

async function handleSave() {
  const valid = await dialogFormRef.value?.validate().catch(() => false)
  if (!valid) return

  dialog.saving = true
  try {
    if (dialog.mode === 'editSelf') {
      // 编辑自己：名称/密码（不含过期日期）
      const updated = await updateMe({
        nickname: dialog.form.nickname || undefined,
        password: dialog.form.password || undefined,
      })
      setUser(updated)
      ElMessage.success('个人信息已更新')
    } else if (dialog.mode === 'editOther') {
      // 编辑他人：只能设置过期日期
      await updateUser(dialog.userId, { expires_at: dialog.form.expires_at || null })
      ElMessage.success('过期日期已更新')
    } else {
      await createUser({
        email: dialog.form.email.trim(),
        password: dialog.form.password,
        nickname: dialog.form.nickname || undefined,
        expires_at: dialog.form.expires_at || null,
      })
      ElMessage.success('用户创建成功')
    }
    dialog.visible = false
    loadData()
  } catch {
    // 错误信息由拦截器统一提示
  } finally {
    dialog.saving = false
  }
}

async function toggleStatus(row: UserTreeNode) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  await updateUser(row.id, { status: next })
  ElMessage.success(next === 'active' ? '已启用' : '已禁用')
  loadData()
}

async function handleDelete(row: UserTreeNode) {
  await deleteUser(row.id)
  ElMessage.success('用户已删除')
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.page-container {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  align-items: center;
}

.spacer {
  flex: 1;
}

.email {
  margin-right: 6px;
}

.owner-tag {
  vertical-align: middle;
}

.expired {
  color: #f56c6c;
}

.never {
  color: #909399;
}

.self-tip {
  font-size: 12px;
  color: #909399;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}
</style>
