<template>
  <div class="task-page">
    <div class="page-toolbar mb-4">
      <el-button :icon="Refresh" @click="fetchTasks">刷新</el-button>
    </div>

    <el-card>
      <el-form :inline="true" class="mb-4">
        <el-form-item label="任务类型">
          <el-select v-model="filterType" placeholder="全部" clearable style="width: 160px">
            <el-option label="文档处理" value="document_process" />
            <el-option label="集合重建" value="collection_rebuild" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filterStatus" placeholder="全部" clearable style="width: 140px">
            <el-option label="等待中" value="pending" />
            <el-option label="运行中" value="running" />
            <el-option label="已完成" value="completed" />
            <el-option label="失败" value="failed" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
        </el-form-item>
      </el-form>

      <el-table :data="tasks" v-loading="loading" stripe>
        <el-table-column prop="type" label="任务类型" width="150">
          <template #default="{ row }">
            {{ typeText(row.type) }}
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="250">
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <el-progress
                :percentage="Math.round(row.progress * 100)"
                :status="progressStatus(row.status)"
                :stroke-width="8"
              />
              <span class="text-sm text-gray-500">{{ (row.progress * 100).toFixed(0) }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="retry_count" label="重试" width="80" align="center" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'running' || row.status === 'pending'"
              type="warning"
              link
              size="small"
              @click="handleCancel(row)"
            >
              取消
            </el-button>
            <el-button
              v-if="row.status === 'failed'"
              type="primary"
              link
              size="small"
              @click="handleRetry(row)"
            >
              重试
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
          @size-change="fetchTasks"
          @current-change="fetchTasks"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { formatAppTime } from '@/utils/datetime'
import request from '@/utils/request'

const tasks = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const filterType = ref('')
const filterStatus = ref('')

async function fetchTasks() {
  loading.value = true
  try {
    const params: any = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (filterType.value) params.type = filterType.value
    if (filterStatus.value) params.status = filterStatus.value

    const data = await request.get<any, any>('/v1/tasks', { params })
    tasks.value = data.items
    total.value = data.total
  } catch (e) {
    // error
  } finally {
    loading.value = false
  }
}

async function handleCancel(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定要取消任务 "${row.id}" 吗？`,
      '取消确认',
      { type: 'warning' }
    )
    await request.post(`/v1/tasks/${row.id}/cancel`)
    ElMessage.success('任务已取消')
    fetchTasks()
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error(err?.message || '取消失败')
    }
  }
}

function handleRetry(row: any) {
  ElMessage.info('重试功能开发中...')
}

function typeText(type: string) {
  const map: Record<string, string> = {
    document_process: '文档处理',
    collection_rebuild: '集合重建',
    consistency_check: '一致性检查',
  }
  return map[type] || type
}

function statusText(status: string) {
  const map: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    paused: '已暂停',
  }
  return map[status] || status
}

function statusType(status: string) {
  const map: Record<string, string> = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info',
    paused: 'warning',
  }
  return map[status] || 'info'
}

function progressStatus(status: string) {
  const map: Record<string, any> = {
    completed: 'success',
    failed: 'exception',
    running: '',
  }
  return map[status] || undefined
}

function formatDate(date: string) {
  return formatAppTime(date)
}

onMounted(() => {
  fetchTasks()
})
</script>

<style scoped>
.task-page {
  height: 100%;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}
</style>
