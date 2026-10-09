<template>
  <el-dialog
    v-model="visible"
    title="上传文件"
    width="480px"
    :close-on-click-modal="false"
    :show-close="false"
    align-center
  >
    <div class="upload-content">
      <div v-for="(item, index) in uploadList" :key="index" class="upload-item">
        <div class="file-info">
          <el-icon class="file-icon"><Document /></el-icon>
          <span class="file-name" :title="item.fileName">{{ item.fileName }}</span>
          <span class="file-size">{{ formatSize(item.fileSize) }}</span>
        </div>

        <div class="progress-wrapper">
          <el-progress
            :percentage="Math.round(item.progress)"
            :status="progressStatus(item.status)"
            :stroke-width="6"
          />
          <div class="progress-info">
            <span class="status-text">{{ statusText(item.status) }}</span>
            <span v-if="item.status === 'uploading' && item.speed > 0" class="speed">
              {{ formatSpeed(item.speed) }}
            </span>
          </div>
        </div>

        <div class="actions">
          <el-button
            v-if="['pending', 'hashing', 'init', 'uploading', 'merging'].includes(item.status)"
            type="danger"
            text
            size="small"
            @click="handleCancel(index)"
          >
            取消
          </el-button>
          <el-button
            v-if="item.status === 'failed'"
            type="primary"
            text
            size="small"
            @click="handleRetry(index)"
          >
            重试
          </el-button>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="handleClose" :disabled="hasActiveUploads">
        {{ hasActiveUploads ? '上传中...' : '关闭' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import { chunkedUpload, formatFileSize, formatSpeed } from '@/utils/uploader'

interface UploadItem {
  file: File
  fileName: string
  fileSize: number
  progress: number
  speed: number
  status: 'pending' | 'hashing' | 'init' | 'uploading' | 'merging' | 'completed' | 'failed' | 'cancelled'
  abortController: AbortController | null
  error?: string
}

const props = defineProps<{
  modelValue: boolean
  files?: File[]
  collectionId: string
  directoryId?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'success': [result: any]
  'allComplete': []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const uploadList = ref<UploadItem[]>([])

const hasActiveUploads = computed(() => {
  return uploadList.value.some(item =>
    ['pending', 'hashing', 'init', 'uploading', 'merging'].includes(item.status)
  )
})

// 监听 files 变化，开始上传
watch(
  () => props.files,
  (newFiles) => {
    if (newFiles && newFiles.length > 0) {
      startUploads(newFiles)
    }
  },
  { immediate: true }
)

watch(visible, (val) => {
  if (!val && !hasActiveUploads.value) {
    uploadList.value = []
  }
})

function formatSize(bytes: number): string {
  return formatFileSize(bytes)
}

function statusText(status: string): string {
  const map: Record<string, string> = {
    pending: '等待中',
    hashing: '计算校验中',
    init: '初始化中',
    uploading: '上传中',
    merging: '合并中',
    completed: '已完成',
    failed: '上传失败',
    cancelled: '已取消',
  }
  return map[status] || status
}

function progressStatus(status: string): string {
  const map: Record<string, string> = {
    completed: 'success',
    failed: 'exception',
    cancelled: 'warning',
  }
  return map[status] || ''
}

async function startUploads(files: File[]) {
  for (const file of files) {
    const item: UploadItem = {
      file,
      fileName: file.name,
      fileSize: file.size,
      progress: 0,
      speed: 0,
      status: 'pending',
      abortController: null,
    }
    uploadList.value.push(item)
  }

  // 逐个上传
  for (let i = 0; i < uploadList.value.length; i++) {
    if (uploadList.value[i].status === 'pending') {
      await uploadFile(i)
    }
  }

  if (!hasActiveUploads.value) {
    emit('allComplete')
  }
}

async function uploadFile(index: number) {
  const item = uploadList.value[index]
  if (item.status === 'cancelled') return

  item.abortController = new AbortController()

  try {
    const result: any = await chunkedUpload(item.file, {
      collectionId: props.collectionId,
      directoryId: props.directoryId,
      signal: item.abortController.signal,
      onStatusChange: (status) => {
        item.status = status as any
      },
      onProgress: (progress) => {
        item.progress = progress.percent
        item.speed = progress.speed
      },
    })

    item.status = 'completed'
    item.progress = 100
    emit('success', result)
  } catch (err: any) {
    if (err.name === 'AbortError' || err?.code === 'ERR_CANCELED') {
      item.status = 'cancelled'
    } else {
      item.status = 'failed'
      item.error = err.message || '上传失败'
      ElMessage.error(`${item.fileName}: ${item.error}`)
    }
  } finally {
    item.abortController = null
  }
}

async function handleCancel(index: number) {
  const item = uploadList.value[index]
  try {
    await ElMessageBox.confirm(
      `确定要取消上传 "${item.fileName}" 吗？`,
      '取消确认',
      { type: 'warning' }
    )

    if (item.abortController) {
      item.abortController.abort()
    }
  } catch {
    // 用户取消了确认
  }
}

async function handleRetry(index: number) {
  const item = uploadList.value[index]
  item.status = 'pending'
  item.progress = 0
  item.speed = 0
  item.error = undefined
  await uploadFile(index)
}

function handleClose() {
  if (hasActiveUploads.value) {
    ElMessage.warning('还有文件正在上传，请等待或先取消')
    return
  }
  visible.value = false
}
</script>

<style scoped>
.upload-content {
  max-height: 400px;
  overflow-y: auto;
}

.upload-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.upload-item:last-child {
  border-bottom: none;
}

.file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.file-icon {
  color: var(--el-color-primary);
  font-size: 18px;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}

.file-size {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  flex-shrink: 0;
}

.progress-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-wrapper .el-progress {
  flex: 1;
}

.progress-info {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  min-width: 80px;
}

.status-text {
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.speed {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}
</style>
