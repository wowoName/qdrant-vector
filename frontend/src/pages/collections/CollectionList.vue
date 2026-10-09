<template>
  <div class="collections-page">
    <div class="page-toolbar mb-4">
      <el-input
        v-model="keyword"
        placeholder="搜索知识库名称"
        :prefix-icon="Search"
        clearable
        @keyup.enter="fetchData"
        @clear="fetchData"
        class="w-80"
      />
      <el-button v-if="canWriteUser" type="primary" :icon="Plus" @click="showCreateDialog = true">
        新建知识库
      </el-button>
    </div>

    <el-card shadow="hover" class="collection-card">
      <template #header>
        <div class="card-header flex-between">
          <span>知识库列表</span>
          <span class="text-gray-500 text-sm">共 {{ total }} 个</span>
        </div>
      </template>

      <el-table :data="collections" v-loading="loading" stripe>
        <el-table-column prop="name" label="名称" min-width="180">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <el-icon :size="18" color="#409eff"><CollectionIcon /></el-icon>
              <span class="font-medium cursor-pointer" @click="goToDocuments(row)">
                {{ row.name }}
              </span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column prop="embedding_model" label="Embedding 模型" width="180" />
        <el-table-column prop="vector_dim" label="维度" width="80" align="center" />
        <el-table-column prop="document_count" label="文档数" width="100" align="center" />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right" align="center">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="goToDocuments(row)">
              管理
            </el-button>
            <el-button v-if="canWriteUser" type="danger" link size="small" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination mt-4 flex justify-end">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="fetchData"
          @current-change="fetchData"
        />
      </div>
    </el-card>

    <!-- 新建知识库对话框 -->
    <el-dialog v-model="showCreateDialog" title="新建知识库" width="500px">
      <el-form :model="formData" :rules="rules" ref="formRef" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="formData.name" placeholder="请输入知识库名称" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="formData.description" type="textarea" :rows="3" placeholder="请输入描述" />
        </el-form-item>
        <el-form-item label="向量模型" prop="embedding_model">
          <el-select v-model="formData.embedding_model" style="width: 100%" @change="onModelChange">
            <el-option label="BAAI/bge-small-zh-v1.5 (512维，推荐，速度快)" value="BAAI/bge-small-zh-v1.5" />
            <el-option label="BAAI/bge-base-zh-v1.5 (768维，平衡)" value="BAAI/bge-base-zh-v1.5" />
            <el-option label="BAAI/bge-large-zh-v1.5 (1024维，高质量)" value="BAAI/bge-large-zh-v1.5" />
            <el-option label="BAAI/bge-m3 (1024维，多语言)" value="BAAI/bge-m3" />
          </el-select>
        </el-form-item>
        <el-form-item label="向量维度" prop="vector_dim">
          <el-input-number v-model="formData.vector_dim" :min="128" :max="4096" style="width: 100%" />
        </el-form-item>
        <el-form-item label="距离度量" prop="distance_metric">
          <el-select v-model="formData.distance_metric" style="width: 100%">
            <el-option label="Cosine (余弦)" value="cosine" />
            <el-option label="Euclidean (欧氏)" value="euclid" />
            <el-option label="Dot Product (点积)" value="dot" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Plus, Search, Collection as CollectionIcon } from '@element-plus/icons-vue'
import { getCollections, createCollection, deleteCollection, type Collection } from '@/api/collection'
import { canWrite } from '@/utils/user'
import dayjs from 'dayjs'

const router = useRouter()
const canWriteUser = computed(() => canWrite())

const loading = ref(false)
const collections = ref<Collection[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')

const showCreateDialog = ref(false)
const creating = ref(false)
const formRef = ref<FormInstance>()
const formData = ref({
  name: '',
  description: '',
  embedding_model: 'BAAI/bge-small-zh-v1.5',
  vector_dim: 512,
  distance_metric: 'cosine',
})

// 模型与维度的对应关系
const MODEL_DIM_MAP: Record<string, number> = {
  'BAAI/bge-small-zh-v1.5': 512,
  'BAAI/bge-base-zh-v1.5': 768,
  'BAAI/bge-large-zh-v1.5': 1024,
  'BAAI/bge-m3': 1024,
}

function onModelChange(model: string) {
  // 选择模型后自动填充对应的向量维度
  const dim = MODEL_DIM_MAP[model]
  if (dim) {
    formData.value.vector_dim = dim
  }
}

const rules: FormRules = {
  name: [{ required: true, message: '请输入知识库名称', trigger: 'blur' }],
  embedding_model: [{ required: true, message: '请选择 Embedding 模型', trigger: 'change' }],
  vector_dim: [{ required: true, message: '请输入向量维度', trigger: 'blur' }],
  distance_metric: [{ required: true, message: '请选择距离度量', trigger: 'change' }],
}

function formatDate(date: string) {
  return dayjs(date).format('YYYY-MM-DD HH:mm:ss')
}

async function fetchData() {
  loading.value = true
  try {
    const data = await getCollections({ page: page.value, page_size: pageSize.value, keyword: keyword.value })
    collections.value = data.items
    total.value = data.total
  } catch (e) {
    // error handled by interceptor
  } finally {
    loading.value = false
  }
}

function goToDocuments(row: Collection) {
  router.push(`/collections/${row.id}/documents`)
}

async function handleCreate() {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    creating.value = true
    try {
      await createCollection(formData.value)
      ElMessage.success('创建成功')
      showCreateDialog.value = false
      formData.value = {
        name: '',
        description: '',
        embedding_model: 'BAAI/bge-small-zh-v1.5',
        vector_dim: 512,
        distance_metric: 'cosine',
      }
      fetchData()
    } catch (e) {
      // error handled
    } finally {
      creating.value = false
    }
  })
}

async function handleDelete(row: Collection) {
  try {
    await ElMessageBox.confirm(
      `确定要删除知识库 "${row.name}" 吗？所有文档和向量数据都会被删除，此操作不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    await deleteCollection(row.id)
    ElMessage.success('删除成功')
    fetchData()
  } catch {
    // cancelled
  }
}

onMounted(() => {
  fetchData()
})
</script>

<style scoped>
.collections-page {
  height: 100%;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.collection-card {
  height: calc(100% - 100px);
  display: flex;
  flex-direction: column;
}

.card-header {
  font-weight: 600;
}

.pagination {
  margin-top: auto;
}
</style>
