/**
 * 后端接口封装。开发期由 Vite 代理 /api 到 http://localhost:8000。
 */
import axios from 'axios'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 120000
})

export const api = {
  // 元数据与基础数据
  cities: () => http.get('/api/cities').then(r => r.data),
  pois: (city) => http.get('/api/data/pois', { params: { city } }).then(r => r.data),
  roads: (city) => http.get('/api/data/roads', { params: { city } }).then(r => r.data),
  shelters: (city) => http.get('/api/data/shelters', { params: { city } }).then(r => r.data),

  // 分析
  value: (city, weights, resolution) =>
    http.post('/api/analysis/value', { city, weights, resolution }).then(r => r.data),
  site: (city, min_score, top_k, weights) =>
    http.post('/api/analysis/site', { city, min_score, top_k, weights }).then(r => r.data),
  route: (city, start, end, optimize, hazard, mode, vias, alternatives) =>
    http.post('/api/route', { city, start, end, optimize, hazard, mode, vias, alternatives }).then(r => r.data),
  routeAmap: (start, end, optimize, mode, vias, alternatives) =>
    http.post('/api/route/amap', { start, end, optimize, mode, vias, alternatives }).then(r => r.data),
  evacuate: (city, start, hazard, mode) =>
    http.post('/api/evacuate', { city, start, hazard, mode }).then(r => r.data),
  hotspot: (city) => http.post('/api/stats/hotspot', { city }).then(r => r.data),
  serviceArea: (city, center, bands, mode, baidu = false) =>
    http.post('/api/service-area', { city, center, bands, mode, baidu }).then(r => r.data),
  flood: (city, water_level, resolution) =>
    http.post('/api/flood', { city, water_level, resolution }).then(r => r.data),
  floodAnimation: (city, target_level, frames, resolution) =>
    http.post('/api/flood/animation', { city, target_level, frames, resolution }).then(r => r.data),

  // AI 助手（多轮上下文）
  ai: (prompt, city, context_point, session_id = 'default') =>
    http.post('/api/ai', { prompt, city, context_point, session_id }).then(r => r.data),
  aiReset: (session_id = 'default') =>
    http.post('/api/ai/reset', null, { params: { session_id } }).then(r => r.data)
}
