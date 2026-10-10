<template>
  <div class="page-container" v-loading="loading">
    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="toolbar-title">
        <h3>存储概览</h3>
        <p>查看服务器磁盘容量与各租户空间占用情况</p>
      </div>
      <div class="spacer" />
      <span v-if="updatedAt" class="updated">更新于 {{ updatedAt }}</span>
      <el-button :icon="Refresh" @click="loadData">刷新</el-button>
    </div>

    <!-- 超售警告 -->
    <el-alert
      v-if="summary.overcommit"
      class="overcommit-alert"
      type="error"
      :closable="false"
      show-icon
      title="配额已超售"
    >
      <template #default>
        各租户已分配配额合计 {{ formatSize(summary.total_quota) }}，已超过可分配上限
        {{ formatSize(summary.allocatable_capacity) }}（磁盘总容量的 {{ allocRatioLabel }}）。建议调低部分租户配额。
      </template>
    </el-alert>

    <!-- 指标卡片 -->
    <div class="stat-grid">
      <div class="stat-card">
        <span class="stat-label">磁盘总容量</span>
        <span class="stat-value">{{ formatSize(disk.total) }}</span>
        <span class="stat-sub">存储目录 {{ disk.path }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">磁盘已用</span>
        <span class="stat-value">{{ formatSize(disk.used) }}</span>
        <span class="stat-sub">可用 {{ formatSize(disk.free) }}</span>
      </div>
      <div class="stat-card" :class="{ 'stat-danger': summary.overcommit }">
        <span class="stat-label">租户配额总量</span>
        <span class="stat-value">{{ formatSize(summary.total_quota) }}</span>
        <span class="stat-sub">
          {{ summary.tenant_count }} 个租户 · {{ summary.unlimited_count }} 个不限制 · 上限
          {{ formatSize(summary.allocatable_capacity) }}
        </span>
      </div>
      <div class="stat-card">
        <span class="stat-label">租户实际用量</span>
        <span class="stat-value">{{ formatSize(summary.total_used) }}</span>
        <span class="stat-sub">占配额 {{ summary.quota_used_percent }}% · {{ summary.total_documents }} 个文档</span>
      </div>
    </div>

    <!-- 物理磁盘 -->
    <el-card class="disk-card" shadow="never">
      <div class="disk-head">
        <div class="disk-title">
          <span class="card-title">物理磁盘使用情况</span>
          <span class="card-sub">{{ disk.mount }}</span>
        </div>
        <span class="disk-percent" :class="diskPercentClass">{{ disk.percent }}%</span>
      </div>
      <el-progress
        :percentage="disk.percent"
        :stroke-width="12"
        :show-text="false"
        :status="disk.percent >= 90 ? 'exception' : undefined"
      />
      <div class="disk-metrics">
        <div class="metric">
          <span class="m-label">总容量</span>
          <span class="m-value">{{ formatSize(disk.total) }}</span>
        </div>
        <div class="metric">
          <span class="m-label">已使用</span>
          <span class="m-value">{{ formatSize(disk.used) }}</span>
        </div>
        <div class="metric">
          <span class="m-label">可用</span>
          <span class="m-value">{{ formatSize(disk.free) }}</span>
        </div>
      </div>

      <!-- 配额分配情况 -->
      <div class="alloc-block">
        <div class="alloc-head">
          <span class="alloc-label">配额分配情况（可分配上限为磁盘总容量的 {{ allocRatioLabel }}）</span>
          <span class="alloc-value" :class="{ 'is-over': summary.overcommit }">
            {{ formatSize(summary.total_quota) }} / {{ formatSize(summary.allocatable_capacity) }}
          </span>
        </div>
        <el-progress
          :percentage="allocPercent"
          :stroke-width="10"
          :show-text="false"
          :status="summary.overcommit ? 'exception' : undefined"
        />
        <div class="alloc-foot">
          <span v-if="summary.overcommit" class="over">已超售，实际可用受磁盘物理容量限制</span>
          <span v-else>
            剩余可分配 {{ formatSize(summary.allocatable_remaining) }}（磁盘物理剩余
            {{ formatSize(disk.free) }}）
          </span>
        </div>
      </div>
    </el-card>

    <!-- 租户用量 -->
    <el-card class="table-card" shadow="never">
      <template #header>
        <div class="table-head">
          <span class="card-title">租户空间用量</span>
          <el-tag size="small" type="info">共 {{ tenants.length }} 项</el-tag>
        </div>
      </template>
      <el-table :data="tenants" stripe>
        <el-table-column label="租户 / 账号" min-width="260">
          <template #default="{ row }">
            <div class="tenant-cell">
              <span class="tenant-name">{{ row.nickname || row.email }}</span>
              <el-tag v-if="row.role === 'super_admin'" size="small" type="danger">平台</el-tag>
              <el-tag v-else size="small" type="warning">租户</el-tag>
            </div>
            <span class="tenant-email">{{ row.email }}</span>
          </template>
        </el-table-column>
        <el-table-column label="配额" width="130" align="right">
          <template #default="{ row }">
            <span v-if="row.unlimited" class="never">不限制</span>
            <span v-else class="num">{{ formatSize(row.quota || 0) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="已用" width="130" align="right">
          <template #default="{ row }">
            <span class="num">{{ formatSize(row.used) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="使用率" width="220">
          <template #default="{ row }">
            <span v-if="row.unlimited" class="never">不限制</span>
            <div v-else class="usage-cell">
              <el-progress
                class="usage-bar"
                :percentage="Math.min(100, row.percent || 0)"
                :stroke-width="6"
                :show-text="false"
                :status="progressStatus(row.percent)"
              />
              <span class="usage-text num">{{ row.percent ?? 0 }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="文档数" width="90" align="right">
          <template #default="{ row }"><span class="num">{{ row.document_count }}</span></template>
        </el-table-column>
        <el-table-column label="子用户" width="90" align="right">
          <template #default="{ row }"><span class="num">{{ row.user_count }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
              {{ row.status === 'active' ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="到期日期" width="160">
          <template #default="{ row }">
            <span v-if="row.expires_at">{{ formatTime(row.expires_at) }}</span>
            <span v-else class="never">永不过期</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { formatAppTime, nowApp } from '@/utils/datetime'
import {
  getDiskOverview,
  type DiskInfo,
  type DiskSummary,
  type TenantUsage,
} from '@/api/admin'

const loading = ref(false)
const updatedAt = ref('')

const disk = ref<DiskInfo>({
  path: '',
  mount: '',
  total: 0,
  used: 0,
  free: 0,
  percent: 0,
})
const summary = ref<DiskSummary>({
  tenant_count: 0,
  unlimited_count: 0,
  total_quota: 0,
  total_used: 0,
  total_documents: 0,
  quota_used_percent: 0,
  allocatable_capacity: 0,
  allocatable_ratio: 0.7,
  allocatable_remaining: 0,
  overcommit: false,
})
const tenants = ref<TenantUsage[]>([])

const diskPercentClass = computed(() => {
  if (disk.value.percent >= 90) return 'danger'
  if (disk.value.percent >= 75) return 'warn'
  return ''
})

/** 可分配比例文案，如 "70%" */
const allocRatioLabel = computed(() => `${Math.round((summary.value.allocatable_ratio || 0) * 100)}%`)

/** 配额分配占"可分配上限"的百分比（超售时封顶 100） */
const allocPercent = computed(() => {
  const capacity = summary.value.allocatable_capacity || 0
  if (capacity <= 0) return 0
  return Math.min(100, Math.round((summary.value.total_quota / capacity) * 1000) / 10)
})

function formatSize(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatTime(value?: string | null): string {
  return value ? formatAppTime(value, 'YYYY-MM-DD HH:mm') : ''
}

function progressStatus(percent: number | null): 'exception' | 'warning' | undefined {
  if (percent == null) return undefined
  if (percent >= 100) return 'exception'
  if (percent >= 85) return 'warning'
  return undefined
}

async function loadData() {
  loading.value = true
  try {
    const data = await getDiskOverview()
    disk.value = data.disk
    summary.value = data.summary
    tenants.value = data.tenants
    updatedAt.value = nowApp().format('HH:mm:ss')
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.page-container {
  padding: 18px;
  border-radius: var(--vdm-radius);
  background: #ffffff;
  border: 1px solid var(--vdm-border);
}

/* ---------- 工具栏 ---------- */
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.toolbar-title h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  letter-spacing: 0.2px;
}
.toolbar-title p {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--vdm-text-mute);
}
.spacer {
  flex: 1;
}
.updated {
  font-size: 12px;
  color: var(--vdm-text-mute);
}

/* ---------- 指标卡片 ---------- */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
@media (max-width: 1100px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.stat-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px 18px;
  border-radius: var(--vdm-radius);
  border: 1px solid var(--vdm-border);
  background: linear-gradient(180deg, #fbfcfe 0%, #ffffff 62%);
  overflow: hidden;
  transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease;
  animation: vdm-rise 0.5s cubic-bezier(0.2, 0.7, 0.3, 1) both;
}
.stat-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, var(--vdm-accent), var(--vdm-accent-2));
  opacity: 0.85;
}
.stat-card:hover {
  transform: translateY(-2px);
  border-color: var(--vdm-border-strong);
  box-shadow: 0 12px 26px rgba(15, 23, 42, 0.08);
}
.stat-card:nth-child(2) {
  animation-delay: 0.06s;
}
.stat-card:nth-child(3) {
  animation-delay: 0.12s;
}
.stat-card:nth-child(4) {
  animation-delay: 0.18s;
}
.stat-label {
  font-size: 12px;
  color: var(--vdm-text-mute);
  letter-spacing: 0.3px;
}
.stat-value {
  font-family: var(--vdm-font-display);
  font-size: 24px;
  font-weight: 700;
  color: var(--vdm-text);
  line-height: 1.1;
}
.stat-sub {
  font-size: 11.5px;
  color: var(--vdm-text-mute);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ---------- 物理磁盘 ---------- */
.disk-card {
  margin-bottom: 16px;
}
.disk-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 12px;
}
.disk-title {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--vdm-text);
}
.card-sub {
  font-family: var(--vdm-mono);
  font-size: 11px;
  color: var(--vdm-text-mute);
  word-break: break-all;
}
.disk-percent {
  font-family: var(--vdm-font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--vdm-accent);
}
.disk-percent.warn {
  color: #d97706;
}
.disk-percent.danger {
  color: #dc2626;
}
.disk-metrics {
  display: flex;
  gap: 34px;
  margin-top: 14px;
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.m-label {
  font-size: 11.5px;
  color: var(--vdm-text-mute);
}
.m-value {
  font-family: var(--vdm-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--vdm-text-dim);
}

/* ---------- 超售警告 ---------- */
.overcommit-alert {
  margin-bottom: 16px;
  border-radius: var(--vdm-radius);
}
.stat-card.stat-danger {
  border-color: rgba(220, 38, 38, 0.35);
  background: linear-gradient(180deg, #fef6f6 0%, #ffffff 62%);
}
.stat-card.stat-danger::before {
  background: linear-gradient(180deg, #ef4444, #dc2626);
}
.stat-card.stat-danger .stat-value {
  color: #dc2626;
}

/* ---------- 配额分配情况 ---------- */
.alloc-block {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px dashed var(--vdm-border);
}
.alloc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.alloc-label {
  font-size: 12.5px;
  color: var(--vdm-text-dim);
}
.alloc-value {
  font-family: var(--vdm-mono);
  font-size: 12.5px;
  color: var(--vdm-text-dim);
}
.alloc-value.is-over {
  color: #dc2626;
  font-weight: 600;
}
.alloc-foot {
  margin-top: 7px;
  font-size: 11.5px;
  color: var(--vdm-text-mute);
}
.alloc-foot .over {
  color: #dc2626;
}

/* ---------- 租户表格 ---------- */
.table-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.tenant-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tenant-name {
  font-size: 13.5px;
  color: var(--vdm-text);
}
.tenant-email {
  font-size: 11.5px;
  color: var(--vdm-text-mute);
}
.usage-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}
.usage-bar {
  flex: 1;
  min-width: 90px;
}
.usage-text {
  width: 46px;
  text-align: right;
  font-size: 12px;
  color: var(--vdm-text-dim);
}
.num {
  font-family: var(--vdm-mono);
  color: var(--vdm-text-dim);
}
.never {
  color: var(--vdm-text-mute);
}
</style>
