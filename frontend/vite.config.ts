import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import { resolve } from 'path'

export default defineConfig({
  plugins: [
    vue(),
    UnoCSS(),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
    // 强制单实例：防止 vue-file-preview 等依赖引入第二份 Vue 运行时，
    // 否则库内异步渲染器（如 Excel）解析后无法触发重渲染，预览会一直停在“加载中”
    dedupe: ['vue', 'vue-router', 'pinia', 'element-plus'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  worker: {
    format: 'es',
  },
})
