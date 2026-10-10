<template>
  <div class="search-page">
    <el-card class="mb-4">
      <el-form :inline="true" :model="form">
        <el-form-item label="知识库">
          <el-select v-model="form.collection_id" placeholder="请选择知识库" style="width: 260px">
            <el-option label="所有知识库" value="all" />
            <el-option
              v-for="c in collections"
              :key="c.id"
              :label="c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="Top K">
          <el-input-number v-model="form.top_k" :min="1" :max="50" />
        </el-form-item>
        <el-form-item label="相似度阈值">
          <div class="threshold-slider">
            <el-slider v-model="form.score_threshold" :min="0" :max="1" :step="0.05" style="width: 150px" />
            <span class="threshold-value">{{ (form.score_threshold * 100).toFixed(0) }}%</span>
          </div>
        </el-form-item>
      </el-form>

      <div class="search-input flex gap-3 mt-2">
        <el-input
          v-model="form.query"
          placeholder="输入要检索的内容..."
          :prefix-icon="Search"
          size="large"
          @keyup.enter="handleSearch"
        />
        <el-button type="primary" size="large" :icon="Search" :loading="searching" @click="handleSearch">
          搜索
        </el-button>
      </div>
    </el-card>

    <el-card v-if="results.length > 0">
      <template #header>
        <div class="flex-between">
          <span class="font-medium">检索结果 ({{ results.length }}条，耗时 {{ latency }}ms)</span>
        </div>
      </template>

      <div v-for="(item, index) in results" :key="item.chunk_id" class="result-item">
        <div class="result-header flex-between mb-2">
          <div class="flex items-center gap-2">
            <el-tag type="primary" size="small">#{{ index + 1 }}</el-tag>
            <span class="font-medium">{{ item.document_name }}</span>
            <el-tag v-if="form.collection_id === 'all'" size="small" type="warning">{{ getCollectionName(item.collection_id) }}</el-tag>
            <el-tag size="small" type="info">Chunk {{ item.chunk_index }}</el-tag>
            <el-tag v-if="item.page_num !== null" size="small">第 {{ item.page_num }} 页</el-tag>
          </div>
          <div class="score">
            <span class="text-gray-500 text-sm">相似度:</span>
            <span class="score-value" :style="{ color: scoreColor(item.score) }">
              {{ (item.score * 100).toFixed(2) }}%
            </span>
          </div>
        </div>
        <div class="result-text p-3 rounded text-sm leading-relaxed">
          {{ item.text }}
        </div>
        <el-divider v-if="index < results.length - 1" />
      </div>
    </el-card>

    <el-empty v-else-if="!searching && !form.query" description="输入查询内容开始检索" />
    <el-empty v-else-if="!searching && results.length === 0" description="未找到相关结果" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { getCollections, type Collection } from '@/api/collection'
import request from '@/utils/request'

const collections = ref<Collection[]>([])
const searching = ref(false)
const results = ref<any[]>([])
const latency = ref(0)

const form = reactive({
  collection_id: 'all',
  query: '',
  top_k: 10,
  score_threshold: 0.0,
})

async function fetchCollections() {
  try {
    const data = await getCollections({ page: 1, page_size: 100 })
    collections.value = data.items
  } catch (e) {
    // error
  }
}

async function handleSearch() {
  if (!form.query.trim()) {
    ElMessage.warning('请输入检索内容')
    return
  }
  if (!form.collection_id) {
    ElMessage.warning('请选择知识库')
    return
  }

  searching.value = true
  try {
    const data = await request.post<any, any>(
      `/v1/collections/${form.collection_id}/search`,
      {
        query: form.query,
        top_k: form.top_k,
        score_threshold: form.score_threshold,
      }
    )
    results.value = data.results
    latency.value = data.latency_ms
  } catch (e) {
    results.value = []
  } finally {
    searching.value = false
  }
}

function scoreColor(score: number) {
  if (score >= 0.8) return '#16a34a'
  if (score >= 0.6) return '#d97706'
  return '#dc2626'
}

function getCollectionName(id: string) {
  const c = collections.value.find((item) => item.id === id)
  return c ? c.name : id
}

onMounted(() => {
  fetchCollections()
})
</script>

<style scoped>
.search-page {
  max-width: 1000px;
  margin: 0 auto;
}

.search-input :deep(.el-input__wrapper) {
  padding: 8px 15px;
}

.result-item {
  margin-bottom: 0;
}

.score-value {
  font-weight: 600;
  font-size: 16px;
  margin-left: 4px;
}

.threshold-slider {
  display: flex;
  align-items: center;
  gap: 10px;
}

.threshold-value {
  font-size: 13px;
  color: var(--vdm-text-dim);
  min-width: 40px;
  text-align: right;
}

.result-text {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
  color: var(--vdm-text-dim);
  background: #f7f8fa;
  border: 1px solid var(--vdm-border);
}
</style>
