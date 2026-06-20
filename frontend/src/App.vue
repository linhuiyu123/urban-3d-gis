<template>
  <div class="app-root">
    <div ref="cesiumEl" class="cesium-container"></div>

    <!-- 顶栏 -->
    <header class="topbar glass">
      <div class="brand">
        <span class="logo">◢◣</span>
        <h1 class="title-gradient">城市三维可视化与分析平台</h1>
      </div>
      <div class="top-right">
        <div class="seg-group">
          <label class="seg-label">研究区</label>
          <button v-for="(c, key) in cities" :key="key" class="btn sm"
            :class="{ active: key === currentCity }" @click="switchCity(key)">{{ c.name }}</button>
        </div>
        <div class="seg-group">
          <label class="seg-label">底图</label>
          <button class="btn sm" :class="{ active: basemap === 'satellite_labels' }" @click="setBase('satellite_labels')">影像注记</button>
          <button class="btn sm" :class="{ active: basemap === 'satellite' }" @click="setBase('satellite')">纯影像</button>
          <button class="btn sm" :class="{ active: basemap === 'street' }" @click="setBase('street')">街道</button>
        </div>
        <span class="tag">AI · DeepSeek</span>
      </div>
    </header>

    <ControlPanel :poiCn="poiCn" :picks="picks" :busy="busy" :pendingPick="pendingPick" :terrainOn="terrainOn"
      @run="onRun" @pick="startPick" @toggle-poi="togglePoi" @toggle-height="toggleHeight"
      @sun="setSun" @shadows="toggleShadows" @terrain="setTerrainOn" @clear="clearAll" @clear-vias="clearVias" />

    <ResultPanel :result="result" />

    <AIChat :city="currentCity" :contextPoint="picks.lastClick" @result="onAIResult" />

    <div v-if="hint" class="hint glass">{{ hint }}</div>
  </div>
</template>

<script>
import ControlPanel from './components/ControlPanel.vue'
import AIChat from './components/AIChat.vue'
import ResultPanel from './components/ResultPanel.vue'
import { api } from './api'
import {
  createViewer, loadOsmBuildings, flyToCity, onMapClick,
  styleBuildingsByHeight, setSunlight, setBasemap, setShadows, setTerrain
} from './cesium/viewer'
import { LayerManager } from './cesium/layers'
import { viewshed } from './cesium/analysis3d'

// 选点种类 → 标记样式
const PICK_META = {
  'route-start': { k: 'routeStart', label: '起点', color: '#7cf6c8' },
  'route-end': { k: 'routeEnd', label: '终点', color: '#4da3ff' },
  'evac-start': { k: 'evacStart', label: '撤离起点', color: '#ff5c7c' },
  'iso-center': { k: 'isoCenter', label: '设施中心', color: '#4da3ff' },
  'observer': { k: 'observer', label: '观察点', color: '#ffd24d' }
}

export default {
  name: 'App',
  components: { ControlPanel, AIChat, ResultPanel },
  data() {
    return {
      cities: {}, poiCn: {}, currentCity: 'hangzhou_core',
      viewer: null, tileset: null, lm: null,
      heightStyle: false, poiShown: false, basemap: 'satellite_labels', darkNight: true, sunHour: 9, shadowsOn: true, terrainOn: true,
      busy: false, hint: '', result: null, pendingPick: null,
      picks: { routeStart: null, routeEnd: null, evacStart: null, isoCenter: null, observer: null, lastClick: null, routeVias: [] },
      floodHazard: null, _floodTimer: null
    }
  },
  async mounted() {
    const meta = await api.cities()
    this.cities = meta.cities
    this.poiCn = meta.poi_cn
    this.currentCity = meta.default

    this.viewer = await createViewer(this.$refs.cesiumEl)
    this.lm = new LayerManager(this.viewer)
    this.tileset = await loadOsmBuildings(this.viewer)
    flyToCity(this.viewer, this.cities[this.currentCity])
    setSunlight(this.viewer, this.sunHour, this.darkNight)

    onMapClick(this.viewer, (lonlat) => this.handleClick(lonlat))
    this.flash('就绪。左侧选择分析模块，地图上点选位置会出现标记；右下角可用自然语言提问。')
  },
  methods: {
    flash(msg, ms = 3800) { this.hint = msg; clearTimeout(this._t); this._t = setTimeout(() => this.hint = '', ms) },

    switchCity(key) {
      this.currentCity = key
      this.clearAll()
      flyToCity(this.viewer, this.cities[key])
      if (!this.terrainOn) this.setTerrainOn(false)   // 平面模式下按新城重算白模下移量
      this.flash(`已切换到 ${this.cities[key].name}（${this.cities[key].scale}）`)
    },
    async setBase(type) {
      this.basemap = type
      await setBasemap(this.viewer, type)
      this.setTerrainOn(type !== 'street')   // 街道底图自动关地形，影像底图开启起伏
      setSunlight(this.viewer, this.sunHour, this.darkNight)
    },
    async setTerrainOn(on) {
      this.terrainOn = on
      await setTerrain(this.viewer, on, this.tileset, this.cities[this.currentCity]?.center)
    },

    startPick(target) { this.pendingPick = target; this.flash('请在地图上点选位置…') },
    handleClick(lonlat) {
      this.picks.lastClick = lonlat
      if (!this.pendingPick) return
      if (this.pendingPick === 'route-via') {          // 途径点：追加到数组
        this.picks.routeVias.push(lonlat)
        const idx = this.picks.routeVias.length - 1
        this.lm.marker('via' + idx, lonlat, '途' + (idx + 1), '#ffd24d')
        this.flash(`已添加途径点 ${idx + 1}`)
        this.pendingPick = null
        return
      }
      if (PICK_META[this.pendingPick]) {
        const m = PICK_META[this.pendingPick]
        this.picks[m.k] = lonlat
        this.lm.marker(m.k, lonlat, m.label, m.color)   // 醒目标记
        this.flash(`已标记${m.label}：[${lonlat[0]}, ${lonlat[1]}]`)
        this.pendingPick = null
      }
    },
    clearVias() {
      this.picks.routeVias.forEach((_, i) => this.lm.clearMarker('via' + i))
      this.picks.routeVias = []
      this.flash('已清空途径点')
    },

    async togglePoi() {
      this.poiShown = !this.poiShown
      if (this.poiShown) { this.lm.renderPois(await api.pois(this.currentCity), this.poiCn) }
      else this.lm.clear('pois')
    },
    toggleHeight() {
      this.heightStyle = !this.heightStyle
      styleBuildingsByHeight(this.tileset, this.heightStyle)
      this.flash(this.heightStyle ? '已按建筑高度着色（天际线）' : '已恢复白模')
    },
    setSun({ hour, darkNight }) {
      this.sunHour = hour; this.darkNight = darkNight
      setSunlight(this.viewer, hour, darkNight)
    },
    toggleShadows(on) { this.shadowsOn = on; setShadows(this.viewer, on) },
    clearAll() { this._stopFloodAnim(); this.lm.clearAll(); this.result = null; this.floodHazard = null; this.picks.routeVias = [] },

    async onRun(action) {
      this.busy = true
      try { await this[action.fn](action.payload || {}) }
      catch (e) { this.flash('分析失败：' + (e?.response?.data?.detail || e.message), 5000) }
      finally { this.busy = false }
    },

    async runValue({ weights, resolution }) {
      const fc = await api.value(this.currentCity, weights, resolution)
      this.lm.clear('hotspot'); this.lm.renderValueGrid(fc)
      this.result = { kind: 'value', meta: fc.meta }
    },
    async runSite({ minScore, topK, weights, resolution }) {
      const fc = await api.site(this.currentCity, minScore, topK, weights, resolution)
      this.lm.renderValueGrid(fc, 'value')
      this.result = { kind: 'site', meta: fc.meta }
      this.flash(`选址：找到 ${fc.meta.count} 个达标地块`)
    },
    async runRoute({ optimize, mode }) {
      if (!this.picks.routeStart || !this.picks.routeEnd) return this.flash('请先在地图选起点和终点')
      const fc = await api.route(this.currentCity, this.picks.routeStart, this.picks.routeEnd,
        optimize, null, mode, this.picks.routeVias)
      if (!fc.features.length) return this.flash(fc.meta?.error || '未找到可达路径，可能超出已抓取的路网范围')
      this.lm.renderRoute(fc, 'route', '#4da3ff')
      this.result = { kind: 'route', meta: fc.features[0].properties }
    },
    async runEvacuate({ mode } = {}) {
      if (!this.picks.evacStart) return this.flash('请先在地图选撤离起点')
      const fc = await api.evacuate(this.currentCity, this.picks.evacStart, this.floodHazard, mode)
      if (!fc.features.length) return this.flash(fc.meta?.error || '未找到可达避难场所')
      this.lm.renderRoute(fc, 'route', '#ff5c7c')
      this.result = { kind: 'evacuate', meta: fc.meta }
    },
    async runHotspot({ weights, resolution, attr, k } = {}) {
      const fc = await api.hotspot(this.currentCity, weights, resolution, k, attr)
      this.lm.clear('value'); this.lm.renderHotspot(fc)
      this.result = { kind: 'hotspot', meta: fc.meta }
    },
    async runServiceArea({ bands, mode }) {
      if (!this.picks.isoCenter) return this.flash('请先在地图选设施中心点')
      const fc = await api.serviceArea(this.currentCity, this.picks.isoCenter, bands, mode)
      this.lm.renderIsochrone(fc)
      this.result = { kind: 'iso', meta: fc.meta }
    },
    async runFlood({ waterLevel, reroute, resolution }) {
      this._stopFloodAnim()
      const res = await api.flood(this.currentCity, waterLevel, resolution)
      this.lm.renderFlood(res)
      this.floodHazard = res.hazard
      this.result = { kind: 'flood', meta: res.meta }
      if (reroute && this.picks.evacStart) {
        const ev = await api.evacuate(this.currentCity, this.picks.evacStart, res.hazard)
        if (ev.features.length) { this.lm.renderRoute(ev, 'route', '#ff5c7c'); this.flash('已避开淹没区重算撤离路线') }
      }
    },
    async runFloodAnim({ waterLevel, resolution }) {
      this._stopFloodAnim()
      this.flash('正在计算涨水过程…')
      const res = await api.floodAnimation(this.currentCity, waterLevel, 9, Math.min(resolution || 90, 110))
      const frames = res.frames || []
      if (!frames.length) return this.flash('涨水动画无数据')
      let i = 0
      const play = () => {
        const f = frames[i]
        this.lm.renderFlood({ surface: f.surface, meta: { water_level: f.water_level } })
        this.result = { kind: 'flood', meta: { water_level: f.water_level, flooded_area_km2: f.flooded_area_km2, resolution: res.meta.resolution } }
        if (i >= frames.length - 1) { this._stopFloodAnim(); this.floodHazard = f.hazard; this.flash(`涨水过程完成（峰值 ${f.water_level} m）`) }
        i++
      }
      play()
      this._floodTimer = setInterval(play, 750)
    },
    _stopFloodAnim() { if (this._floodTimer) { clearInterval(this._floodTimer); this._floodTimer = null } },
    async runViewshed({ eyeHeight, radius }) {
      if (!this.picks.observer) return this.flash('请先在地图选观察点')
      this.flash('三维视域计算中…')
      const stat = await viewshed(this.viewer, this.lm, this.picks.observer, eyeHeight, radius)
      this.result = { kind: 'viewshed', meta: stat }
    },

    onAIResult(r) {
      if (!r || !r.layer) return
      const k = r.kind
      if (k === 'value' || k === 'site') this.lm.renderValueGrid(r.layer)
      else if (k === 'hotspot') { this.lm.clear('value'); this.lm.renderHotspot(r.layer) }
      else if (k === 'route') this.lm.renderRoute(r.layer, 'route', '#4da3ff')
      else if (k === 'evacuate') this.lm.renderRoute(r.layer, 'route', '#ff5c7c')
      else if (k === 'isochrone') this.lm.renderIsochrone(r.layer)
      else if (k === 'flood') { this.lm.renderFlood(r.layer); this.floodHazard = r.layer.hazard }
      this.result = { kind: k === 'isochrone' ? 'iso' : k, meta: r.layer.meta, ai: true, tool: r.tool }
    }
  }
}
</script>

<style scoped>
.app-root { width: 100%; height: 100%; position: relative; }
.cesium-container { position: absolute; inset: 0; }
.topbar {
  position: absolute; top: 14px; left: 14px; right: 14px; height: 56px;
  display: flex; align-items: center; justify-content: space-between; padding: 0 18px; z-index: 10;
}
.brand { display: flex; align-items: center; gap: 12px; }
.logo { color: var(--accent); font-size: 18px; }
.topbar h1 { font-size: 17px; font-weight: 700; letter-spacing: .5px; white-space: nowrap; }
.top-right { display: flex; align-items: center; gap: 16px; }
.seg-group { display: flex; align-items: center; gap: 6px; }
.seg-label { font-size: 11px; color: var(--text-dim); margin-right: 2px; }
.hint {
  position: absolute; bottom: 18px; left: 50%; transform: translateX(-50%);
  padding: 10px 18px; font-size: 13px; z-index: 20; max-width: 60%; text-align: center;
}
</style>
