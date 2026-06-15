import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import cesium from 'vite-plugin-cesium'

// vite-plugin-cesium 负责自动拷贝 Cesium 的静态资源（Workers/Assets/Widgets）
export default defineConfig({
  plugins: [vue(), cesium()],
  server: {
    port: 5173,
    proxy: {
      // 把 /api 代理到后端，避免跨域问题
      '/api': 'http://localhost:8000'
    }
  }
})
