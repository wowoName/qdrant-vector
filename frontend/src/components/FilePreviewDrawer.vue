<template>
  <el-drawer
    v-model="visible"
    :title="fileName"
    direction="rtl"
    size="80%"
    destroy-on-close
    class="file-preview-drawer"
    @opened="handleDrawerOpened"
  >
    <template v-if="fileInfo">
      <!-- 文件信息头部 -->
      <div class="file-info-bar">
        <div class="file-meta">
          <el-tag size="small" type="info">{{ fileInfo.file_type }}</el-tag>
          <span class="file-size">{{ formatSize(fileInfo.file_size) }}</span>
        </div>
        <div class="file-actions">
          <el-button
            type="primary"
            link
            size="small"
            @click="handleOpenNewWindow"
          >
            <el-icon><RefreshRight /></el-icon>
            <span>新窗口打开</span>
          </el-button>
          <el-button type="primary" link size="small" @click="handleDownload">
            <el-icon><Download /></el-icon>
            <span>下载</span>
          </el-button>
        </div>
      </div>

      <!-- 预览区域 -->
      <div ref="previewContainerRef" class="preview-container">
        <!-- 加载中 -->
        <div v-if="loading" class="preview-loading">
          <el-icon class="is-loading" :size="40"><Loading /></el-icon>
          <p class="mt-2">正在加载预览...</p>
        </div>

        <!-- 预览组件：只有抽屉完全打开后才渲染，确保容器尺寸正确 -->
        <div v-show="!loading" class="preview-content" :key="previewKey">
          <FilePreviewEmbed
            v-if="canPreview && drawerReady"
            :files="previewFiles"
            :current-index="0"
            theme="light"
            locale="zh-CN"
            :show-download="false"
            :show-close="false"
            :show-navigation="false"
            :headless="false"
            class="file-preview-embed"
            @close="visible = false"
          />
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Download, RefreshRight, Loading,
} from '@element-plus/icons-vue'
import { FilePreviewEmbed } from '@eternalheart/vue-file-preview'
import '@eternalheart/vue-file-preview/style.css'
import { getToken } from '@/utils/user'

interface FileInfo {
  id: string
  name: string
  file_type: string
  file_size: number
  status: string
}

const props=defineProps<{
  fileInfo: FileInfo | null
}>()

const visible = defineModel<boolean>('modelValue', { default: false })

const loading = ref(false)
const drawerReady = ref(false)
const previewKey = ref(0)
const previewContainerRef = ref<HTMLElement | null>(null)

// 支持的预览类型（组件内置了 Doc、Docx、Xls、Xlsx、Ppt、Pptx、Pdf、图片、音视频等）
const unsupportedTypes: string[] = []  // 组件支持的格式非常多，默认都尝试预览

const fileName = computed(() => props.fileInfo?.name || '文件预览')

const canPreview = computed(() => {
  if (!props.fileInfo) return false
  const type = props.fileInfo.file_type?.toLowerCase() || ''
  const ext = props.fileInfo.name.split('.').pop()?.toLowerCase() || ''
  // 除了明确不支持的，都尝试预览（组件内部有 Unsupported 兜底）
  return !unsupportedTypes.includes(type) && !unsupportedTypes.includes(ext)
})

// 新窗口打开/下载时浏览器不会携带 Authorization 头，改用 ?token= 传递 JWT
const authQuery = computed(() => `token=${encodeURIComponent(getToken())}`)

const downloadUrl = computed(() => {
  if (!props.fileInfo) return ''
  return `/api/v1/documents/${props.fileInfo.id}/download?${authQuery.value}`
})

const previewUrl = computed(() => {
  if (!props.fileInfo) return ''
  // 在 URL 末尾追加编码后的文件名，帮助预览库识别文件类型
  const encodedName = encodeURIComponent(props.fileInfo.name)
  return `/api/v1/documents/${props.fileInfo.id}/download/${encodedName}?preview=true&${authQuery.value}`
})

const mimeTypeMap: Record<string, string> = {
  // 图片
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  tiff: 'image/tiff',
  tif: 'image/tiff',
  svg: 'image/svg+xml',
  ico: 'image/x-icon',
  // 文档
  pdf: 'application/pdf',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  txt: 'text/plain',
  md: 'text/markdown',
  markdown: 'text/markdown',
  // 表格
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  csv: 'text/csv',
  // 演示
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  // 视频
  mp4: 'video/mp4',
  webm: 'video/webm',
  ogg: 'video/ogg',
  avi: 'video/x-msvideo',
  mkv: 'video/x-matroska',
  mov: 'video/quicktime',
  // 音频
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  m4a: 'audio/mp4',
  flac: 'audio/flac',
  aac: 'audio/aac',
  // 其他
  html: 'text/html',
  htm: 'text/html',
  json: 'application/json',
  xml: 'application/xml',
  zip: 'application/zip',
}

function getMimeType(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  return mimeTypeMap[ext] || 'application/octet-stream'
}

const previewFiles = computed(() => {
  if (!props.fileInfo) return []
  return [{
    name: props.fileInfo.name,
    type: getMimeType(props.fileInfo.name),
    url: previewUrl.value,
    size: props.fileInfo.file_size,
  }]
})

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function handleOpenNewWindow() {
  if (!props.fileInfo) return
  window.open(previewUrl.value, '_blank')
}

function handleDownload() {
  if (!props.fileInfo) return
  const a = document.createElement('a')
  a.href = downloadUrl.value
  a.download = props.fileInfo.name
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function handleDrawerOpened() {
  // 抽屉完全打开后，再延迟一点时间确保布局稳定，然后渲染预览组件
  // 这样可以确保图片预览组件的容器有正确的尺寸，图片自适应才能正常工作
  setTimeout(() => {
    drawerReady.value = true
    previewKey.value++
    // 再等一帧，让预览组件渲染完成后再关闭 loading
    requestAnimationFrame(() => {
      loading.value = false
    })
  }, 100)
}

watch(visible, (val) => {
  if (val) {
    loading.value = true
    drawerReady.value = false
  } else {
    // 关闭时重置状态
    drawerReady.value = false
    loading.value = false
  }
})
</script>

<style scoped>
.file-preview-drawer :deep(.el-drawer__header) {
  position: relative;
  z-index: 100;
}

/* 注意：.el-drawer__body 由 Element Plus 渲染且抽屉被 teleport 到 body，
   scoped 的 :deep() 无法命中，相关样式放在文件末尾的非 scoped <style> 中 */

.file-info-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid var(--el-border-color-light);
  background-color: var(--el-bg-color-page);
  flex-shrink: 0;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}

.file-size {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.file-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preview-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
  /* 父级 .el-drawer__body 已设为 flex 列，靠 flex:1 撑满剩余高度 */
  height: 100%;
}

.preview-content {
  width: 100%;
  height: 100%;
}

.preview-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--el-text-color-secondary);
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: var(--el-bg-color);
  z-index: 10;
}

.file-preview-embed {
  width: 100%;
  height: 100%;
}

/* 深度选择器：确保文件预览库的根容器正确占满空间 */
.file-preview-drawer :deep(.vfp-file-preview),
.file-preview-drawer :deep(.vfp-file-preview__content),
.file-preview-drawer :deep(.vfp-file-preview__body) {
  width: 100%;
  height: 100%;
}

/* 确保图片渲染器容器正确占满空间 */
.file-preview-drawer :deep(.vfp-relative.vfp-flex.vfp-items-center.vfp-justify-center.vfp-w-full.vfp-h-full) {
  width: 100%;
  height: 100%;
}

.mt-2 {
  margin-top: 8px;
}
</style>

<!-- 非 scoped：抽屉被 teleport 到 body，scoped 的 :deep() 无法命中 Element Plus 渲染的 body -->
<style>
.file-preview-drawer .el-drawer__body {
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
