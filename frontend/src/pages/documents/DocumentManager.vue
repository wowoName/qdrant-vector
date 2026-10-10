<template>
  <div
    class="document-manager"
    @dragenter.prevent="handleDragEnter"
    @dragover.prevent="handleDragOver"
    @dragleave="handleDragLeave"
    @drop.prevent="handleDrop"
  >
    <!-- 拖拽覆盖层 -->
    <div v-if="isDragging && canWriteUser" class="drag-overlay">
      <div class="drag-overlay-content">
        <el-icon :size="64" color="#2563eb"><Upload /></el-icon>
        <p class="drag-text">松开鼠标上传文件到当前目录</p>
        <p class="drag-hint">支持 PDF、Word、Excel、PPT、图片、音视频等多种格式</p>
      </div>
    </div>

    <!-- 顶部工具栏 -->
    <div class="toolbar flex-between mb-4">
      <div class="flex items-center gap-3">
        <el-button :icon="ArrowLeft" text @click="goBack">返回</el-button>
        <el-divider direction="vertical" />
        <h2 class="text-lg font-bold m-0">{{ collectionName }}</h2>
        <el-tag v-if="collectionId" type="info" size="small">{{ collectionId }}</el-tag>
      </div>
      <div class="flex items-center gap-3">
        <el-button v-if="canWriteUser" type="primary" :icon="Upload" :loading="uploading" @click="triggerUpload">
          上传文档
        </el-button>
        <input
          ref="fileInputRef"
          type="file"
          multiple
          style="display: none"
          accept=".pdf,.doc,.docx,.txt,.md,.markdown,.xls,.xlsx,.csv,.ppt,.pptx,.html,.htm,.jpg,.jpeg,.png,.bmp,.tiff,.tif,.webp,.gif,.mp4,.avi,.mkv,.mov,.webm,.flv,.wmv,.m4v,.mp3,.wav,.m4a,.flac,.aac,.ogg,.wma,.opus,.amr"
          @change="handleFileSelect"
        />
      </div>
    </div>

    <div class="content-wrapper">
      <!-- 左侧目录树 -->
      <div
        class="directory-panel"
        @dragover.stop.prevent="handleDirDragOver"
        @dragleave="handleDirDragLeave"
        @drop.stop.prevent="handleDirDrop"
        :class="{ 'panel-drag-over': isDirDragging }"
      >
        <el-card class="h-full" shadow="never">
          <template #header>
            <div class="flex-between">
              <span class="font-medium">目录</span>
              <el-button v-if="canWriteUser" :icon="Plus" text size="small" @click="handleAddDir">新建</el-button>
            </div>
          </template>
          <el-tree
            :data="directoryTree"
            :props="{ label: 'name', children: 'children' }"
            node-key="id"
            default-expand-all
            @node-click="handleDirClick"
            :expand-on-click-node="false"
            :current-node-key="currentDirId"
            highlight-current
            class="dir-tree"
          >
            <template #default="{ node, data }">
              <span
                class="custom-tree-node"
                @dragover.stop.prevent="handleTreeNodeDragOver($event, data)"
                @dragleave.stop="handleTreeNodeDragLeave($event, data)"
                @drop.stop.prevent="handleTreeNodeDrop($event, data)"
                :class="{ 'node-drag-over': dragOverNodeId === data.id }"
              >
                <el-icon><Folder /></el-icon>
                <span class="node-label">{{ data.name }}</span>
              </span>
            </template>
          </el-tree>
        </el-card>
      </div>

      <!-- 右侧文档列表 -->
      <div class="document-panel">
        <el-card class="h-full" shadow="never">
          <template #header>
            <div class="flex-between">
              <div class="flex items-center gap-3">
                <span class="font-medium">文档列表</span>
                <el-tag size="small" type="info">共 {{ total }} 个</el-tag>
              </div>
              <el-input
                v-model="keyword"
                placeholder="搜索文档"
                :prefix-icon="Search"
                clearable
                size="default"
                class="w-60"
                @keyup.enter="fetchDocuments"
                @clear="fetchDocuments"
              />
            </div>
          </template>

          <el-table :data="documents" v-loading="loading" stripe>
            <el-table-column prop="name" label="文件名" min-width="250">
              <template #default="{ row }">
                <div class="flex items-center gap-2">
                  <el-icon :size="16" :color="getFileColor(row.file_type)">
                    <component :is="getFileIcon(row.file_type)" />
                  </el-icon>
                  <span>{{ row.name }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="file_type" label="类型" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small">{{ row.file_type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="100" align="right">
              <template #default="{ row }">
                {{ formatSize(row.file_size) }}
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">
                  {{ statusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" width="180">
              <template #default="{ row }">
                {{ formatDate(row.updated_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right" align="center">
              <template #default="{ row }">
                <el-button type="primary" link size="small" @click="handleView(row)">查看</el-button>
                <el-button v-if="canWriteUser" type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
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
              @size-change="fetchDocuments"
              @current-change="fetchDocuments"
            />
          </div>
        </el-card>
      </div>
    </div>

    <!-- 文件预览抽屉 -->
    <FilePreviewDrawer v-model="previewVisible" :file-info="currentPreviewFile" />

    <!-- 上传进度对话框 -->
    <UploadProgressDialog
      v-model="uploadDialogVisible"
      :files="pendingFiles"
      :collection-id="collectionId"
      :directory-id="currentDirId"
      @success="onUploadSuccess"
      @all-complete="onAllUploadComplete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, Upload, Plus, Folder, Search,
  Document, Picture, Grid, Reading, VideoCamera, Microphone,
  Files, Notebook,
} from '@element-plus/icons-vue'
import { formatAppTime } from '@/utils/datetime'
import request from '@/utils/request'
import { canWrite, getUser } from '@/utils/user'
import FilePreviewDrawer from '@/components/FilePreviewDrawer.vue'
import UploadProgressDialog from '@/components/UploadProgressDialog.vue'

const route = useRoute()
const router = useRouter()

const canWriteUser = computed(() => canWrite())

const collectionId = computed(() => route.params.id as string)
const collectionName = ref('知识库')
const currentDirId = ref('')
const directoryTree = ref<any[]>([])
const documents = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const loading = ref(false)
const uploading = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const uploadDialogVisible = ref(false)
const pendingFiles = ref<File[]>([])

// 文件预览
const previewVisible = ref(false)
const currentPreviewFile = ref<any>(null)

// 拖拽状态
const isDragging = ref(false)
const isDirDragging = ref(false)
const dragOverNodeId = ref('')
let dragCounter = 0

function handleDragEnter(e: DragEvent) {
  e.preventDefault()
  dragCounter++
  if (!isDragging.value && e.dataTransfer?.types.includes('Files')) {
    isDragging.value = true
  }
}

function handleDragOver(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer) {
    e.dataTransfer.dropEffect = 'copy'
  }
}

function handleDragLeave(e: DragEvent) {
  dragCounter--
  if (dragCounter <= 0) {
    isDragging.value = false
    dragCounter = 0
  }
}

function handleDrop(e: DragEvent) {
  e.preventDefault()
  isDragging.value = false
  dragCounter = 0
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    processDroppedFiles(Array.from(files), currentDirId.value)
  }
}

function handleDirDragOver(e: DragEvent) {
  e.dataTransfer!.dropEffect = 'copy'
  isDirDragging.value = true
}

function handleDirDragLeave() {
  isDirDragging.value = false
}

function handleDirDrop(e: DragEvent) {
  isDirDragging.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    // 拖到目录面板但不在具体节点上，上传到当前选中目录
    processDroppedFiles(Array.from(files), currentDirId.value)
  }
}

function handleTreeNodeDragOver(e: DragEvent, data: any) {
  e.dataTransfer!.dropEffect = 'copy'
  dragOverNodeId.value = data.id
}

function handleTreeNodeDragLeave(e: DragEvent, data: any) {
  if (dragOverNodeId.value === data.id) {
    dragOverNodeId.value = ''
  }
}

function handleTreeNodeDrop(e: DragEvent, data: any) {
  dragOverNodeId.value = ''
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    // 切换到目标目录并上传
    currentDirId.value = data.id
    processDroppedFiles(Array.from(files), data.id)
  }
}

function processDroppedFiles(files: File[], directoryId: string) {
  // 自用号只读：不允许上传（含拖拽）
  if (!canWriteUser.value) {
    ElMessage.warning('自用号仅可查看和使用，不能上传文档')
    return
  }
  // 校验文件类型
  const validFiles = files.filter(file => {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!allowedExtensions.includes(ext || '')) {
      ElMessage.error(`不支持的文件类型: ${file.name}`)
      return false
    }
    return true
  })

  if (validFiles.length === 0) return
  if (!checkStorageQuota(validFiles)) return

  pendingFiles.value = validFiles
  uploadDialogVisible.value = true
  uploading.value = true

  // 如果拖到的目录不是当前目录，刷新列表
  if (directoryId !== currentDirId.value) {
    page.value = 1
    fetchDocuments()
  }
}

function goBack() {
  router.push('/collections')
}

async function fetchDirectories() {
  try {
    const data = await request.get<any, any[]>(`/v1/collections/${collectionId.value}/directories/tree`)
    directoryTree.value = data
  } catch (e) {
    directoryTree.value = []
  }
}

async function fetchCollectionInfo() {
  try {
    const data = await request.get<any, any>(`/v1/collections/${collectionId.value}`)
    collectionName.value = data.name
  } catch (e) {
    // error
  }
}

async function fetchDocuments() {
  loading.value = true
  try {
    const params: any = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (currentDirId.value) params.directory_id = currentDirId.value
    if (keyword.value) params.keyword = keyword.value

    const data = await request.get<any, any>(
      `/v1/collections/${collectionId.value}/documents`,
      { params }
    )
    documents.value = data.items
    total.value = data.total
  } catch (e) {
    // error
  } finally {
    loading.value = false
  }
}

function handleDirClick(data: any) {
  currentDirId.value = data.id
  page.value = 1
  fetchDocuments()
}

const allowedExtensions = [
  'pdf', 'doc', 'docx', 'txt', 'md', 'markdown',
  'xls', 'xlsx', 'csv',
  'ppt', 'pptx',
  'html', 'htm',
  'jpg', 'jpeg', 'png', 'bmp', 'tiff', 'tif', 'webp', 'gif',
  'mp4', 'avi', 'mkv', 'mov', 'webm', 'flv', 'wmv', 'm4v',
  'mp3', 'wav', 'm4a', 'flac', 'aac', 'ogg', 'wma', 'opus', 'amr',
]

function triggerUpload() {
  fileInputRef.value?.click()
}

function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return

  const files = Array.from(input.files)

  // 校验文件类型
  const validFiles = files.filter(file => {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!allowedExtensions.includes(ext || '')) {
      ElMessage.error(`不支持的文件类型: ${file.name}`)
      return false
    }
    return true
  })

  if (validFiles.length === 0) {
    input.value = ''
    return
  }

  // 存储配额前置校验：空间不足直接提示，避免上传后失败
  if (!checkStorageQuota(validFiles)) {
    input.value = ''
    return
  }

  // 打开上传对话框并开始上传
  pendingFiles.value = validFiles
  uploadDialogVisible.value = true
  uploading.value = true

  // 清空 input 以便下次选择相同文件能触发 change
  input.value = ''
}

/**
 * 上传前校验当前租户剩余存储空间（本地缓存的用量为近似值，最终以后端校验为准）
 */
function checkStorageQuota(files: File[]): boolean {
  const user = getUser()
  if (!user || user.storage_unlimited || !user.storage_quota) return true
  const total = files.reduce((sum, file) => sum + file.size, 0)
  const remain = Math.max(0, user.storage_quota - (user.storage_used || 0))
  if (total > remain) {
    ElMessage.error(
      `存储空间不足：剩余 ${formatSize(remain)}，本次需要 ${formatSize(total)}，请清理文件或联系管理员扩容`
    )
    return false
  }
  return true
}

function onUploadSuccess() {
  ElMessage.success('上传成功，正在处理中...')
  fetchDocuments()
}

function onAllUploadComplete() {
  uploading.value = false
  fetchDocuments()
}

async function handleAddDir() {
  try {
    const { value } = await ElMessageBox.prompt('请输入目录名称', '新建目录', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputPattern: /.+/,
      inputErrorMessage: '目录名称不能为空',
    })
    await request.post(`/v1/collections/${collectionId.value}/directories`, {
      name: value,
      parent_id: currentDirId.value || null,
    })
    ElMessage.success('创建成功')
    fetchDirectories()
  } catch {
    // cancelled
  }
}

async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定要删除文档 "${row.name}" 吗？`,
      '删除确认',
      { type: 'warning' }
    )
    await request.delete(`/v1/documents/${row.id}`)
    ElMessage.success('删除成功')
    fetchDocuments()
  } catch {
    // cancelled
  }
}

function handleView(row: any) {
  if (row.status !== 'completed') {
    ElMessage.warning('文档尚未处理完成，无法预览')
    return
  }
  currentPreviewFile.value = row
  previewVisible.value = true
}

function statusText(status: string) {
  const map: Record<string, string> = {
    pending: '等待中',
    processing: '处理中',
    completed: '已完成',
    failed: '失败',
  }
  return map[status] || status
}

function statusType(status: string) {
  const map: Record<string, string> = {
    pending: 'info',
    processing: 'warning',
    completed: 'success',
    failed: 'danger',
  }
  return map[status] || 'info'
}

function getFileIcon(type: string) {
  const map: Record<string, any> = {
    pdf: Document,
    docx: Reading,
    doc: Reading,
    txt: Document,
    markdown: Notebook,
    xlsx: Grid,
    xls: Grid,
    excel: Grid,
    csv: Grid,
    pptx: Files,
    ppt: Files,
    html: Document,
    htm: Document,
    image: Picture,
    video: VideoCamera,
    audio: Microphone,
  }
  return map[type] || Document
}

function getFileColor(type: string) {
  const map: Record<string, string> = {
    pdf: '#f56c6c',
    docx: '#409eff',
    doc: '#409eff',
    txt: '#909399',
    markdown: '#67c23a',
    xlsx: '#e6a23c',
    xls: '#e6a23c',
    excel: '#e6a23c',
    csv: '#e6a23c',
    pptx: '#f56c6c',
    ppt: '#f56c6c',
    html: '#67c23a',
    htm: '#67c23a',
    image: '#409eff',
    video: '#909399',
    audio: '#e6a23c',
  }
  return map[type] || '#909399'
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatDate(date: string) {
  return formatAppTime(date)
}

onMounted(() => {
  fetchCollectionInfo()
  fetchDirectories()
  fetchDocuments()
})
</script>

<style scoped>
.document-manager {
  height: 100%;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* 拖拽覆盖层 */
.drag-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(37, 99, 235, 0.08);
  border: 4px dashed #2563eb;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  backdrop-filter: blur(4px);
}

.drag-overlay-content {
  text-align: center;
  background-color: #ffffff;
  border: 1px solid var(--vdm-border);
  box-shadow: 0 20px 50px rgba(15, 23, 42, 0.14);
  padding: 48px 64px;
  border-radius: 18px;
}

.drag-text {
  font-size: 20px;
  font-weight: 600;
  color: var(--vdm-text);
  margin: 16px 0 8px;
}

.drag-hint {
  font-size: 14px;
  color: var(--vdm-text-dim);
  margin: 0;
}

/* 目录面板拖拽高亮 */
.panel-drag-over {
  outline: 2px dashed #2563eb;
  outline-offset: -2px;
  border-radius: 4px;
}

/* 树节点拖拽高亮 */
.node-drag-over {
  background-color: rgba(37, 99, 235, 0.14);
  border-radius: 4px;
  padding: 2px 4px;
  margin: -2px -4px;
}

.toolbar {
  flex-shrink: 0;
}

.content-wrapper {
  flex: 1;
  display: flex;
  gap: 16px;
  min-height: 0;
}

.directory-panel {
  width: 260px;
  flex-shrink: 0;
}

.document-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.h-full {
  height: 100%;
}

.dir-tree {
  height: calc(100% - 60px);
  overflow-y: auto;
}

.custom-tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
}

.node-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pagination {
  margin-top: auto;
}
</style>
