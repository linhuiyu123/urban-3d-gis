<template>
  <aside class="result glass" v-if="result">
    <div class="head">
      <h3>{{ titleMap[result.kind] || '分析结果' }}</h3>
      <span v-if="result.ai" class="tag">AI 触发 · {{ result.tool }}</span>
    </div>

    <!-- 价值评估 -->
    <div v-if="result.kind === 'value'">
      <div class="legend"><span>低</span><div class="bar value-bar"></div><span>高</span></div>
      <div class="kv"><span>平均分 / 最高分</span><b>{{ result.meta.score_mean }} / {{ result.meta.score_max }}</b></div>
      <div class="kv"><span>90 分位 / 分辨率</span><b>{{ result.meta.score_p90 }} / {{ result.meta.resolution }}²</b></div>
      <p class="note">颜色 / 柱高 = 综合地段价值（0–100，越暖越高）。改各设施权重会改变结果。</p>
    </div>

    <!-- 选址 -->
    <div v-else-if="result.kind === 'site'">
      <div class="legend"><span>低</span><div class="bar value-bar"></div><span>高</span></div>
      <div class="kv"><span>达标阈值</span><b>≥ {{ result.meta.min_score }}</b></div>
      <div class="kv"><span>达标地块</span><b>{{ result.meta.qualified }} 个</b></div>
      <div class="kv"><span>当前显示</span><b>{{ result.meta.count }} 个</b></div>
      <div class="kv"><span>最高分</span><b>{{ result.meta.top_score }}</b></div>
      <p v-if="result.meta.qualified === 0" class="note">没有达标地块：可降低阈值，或在「价值评估」里调高相关设施权重 / 选其它「重点设施」后重试。</p>
    </div>

    <!-- 热点 -->
    <div v-else-if="result.kind === 'hotspot'">
      <div class="kv"><span>统计对象</span><b>{{ hotspotAttrLabel(result.meta.attr) }}</b></div>
      <div class="kv"><span>显著性阈值</span><b>{{ result.meta.confidence || '90%' }} / |z| ≥ {{ result.meta.z_threshold ?? 1.65 }}</b></div>
      <div class="kv"><span>Moran's I</span><b>{{ result.meta.moran_I }}</b></div>
      <div class="kv"><span>显著性 p</span><b>{{ result.meta.moran_p ?? '—' }}</b></div>
      <div class="kv"><span>热点 / 冷点</span><b>{{ result.meta.n_hot }} / {{ result.meta.n_cold }}</b></div>
      <p class="note">{{ result.meta.interpretation }}（引擎：{{ result.meta.engine }}）</p>
      <div class="legend-list">
        <span><i class="dot" style="background:#ff5c7c"></i>热点（高值聚集）</span>
        <span><i class="dot" style="background:#4682f0"></i>冷点（低值聚集）</span>
        <span><i class="dot" style="background:#8c96aa"></i>不显著</span>
      </div>
    </div>

    <!-- 路径 -->
    <div v-else-if="result.kind === 'route'">
      <div class="kv"><span>交通方式</span><b>{{ result.meta?.mode_cn || '驾车' }}</b></div>
      <div class="kv"><span>路线来源</span><b>{{ result.meta?.source === 'amap' ? '高德在线（含路况）' : '内置路网' }}</b></div>
      <div class="kv"><span>距离</span><b>{{ result.meta?.length_m != null ? (result.meta.length_m/1000).toFixed(2) + ' km' : '—' }}</b></div>
      <div class="kv"><span>预计时间</span><b>{{ result.meta?.time_min != null ? result.meta.time_min + ' 分钟' : '—' }}</b></div>
      <div v-if="result.meta?.fee" class="kv"><span>票价</span><b>{{ result.meta.fee }} 元</b></div>

      <!-- 公交/地铁：分段换乘（颜色与地图对应） -->
      <div v-if="result.meta?.legs && result.meta.legs.length">
        <p class="note">换乘（颜色对应地图线路）：</p>
        <div class="kv" v-for="(l, i) in result.meta.legs" :key="i">
          <span><i class="dot" :style="{ background: l.color }"></i>{{ l.type_cn }}</span>
          <b>{{ l.name }}</b>
        </div>
      </div>

      <!-- 驾车/自研：优化目标 + 多备选切换 -->
      <template v-else>
        <div class="kv"><span>优化目标</span><b>{{ result.meta?.optimize === 'length' ? '最短' : '最快' }}</b></div>
        <div v-if="result.meta?.alts && result.meta.alts.length > 1">
          <p class="note">点击切换高亮路线：</p>
          <div class="kv alt-row" v-for="(a, i) in result.meta.alts" :key="i"
               :class="{ active: activeAlt === i }" @click="pickAlt(i)">
            <span><i class="dot" :style="{ background: altColor(i) }"></i>{{ a.strategy_cn }}</span>
            <b>{{ a.length_km }} km / {{ a.time_min }} 分钟</b>
          </div>
        </div>
      </template>
    </div>

    <!-- 撤离 -->
    <div v-else-if="result.kind === 'evacuate'">
      <div class="kv"><span>目标避难所</span><b>{{ result.meta.shelter || '—' }}</b></div>
      <div class="kv"><span>可达时间 · 交通</span><b>{{ result.meta?.time_min }} 分 · {{ result.meta?.mode_cn || '驾车' }}</b></div>
      <div class="kv"><span>容量 / 候选数</span><b>{{ result.meta?.capacity }} 人 / {{ result.meta?.candidates }}</b></div>
      <p class="note">{{ result.meta?.reason || '已规划就近撤离路线，避开危险区。' }}</p>
    </div>

    <!-- 服务区 -->
    <div v-else-if="result.kind === 'iso'">
      <p class="note">从中心点出发各时间档可达范围（{{ result.meta?.mode_cn || '驾车' }}；{{ isoProvider }}）。</p>
      <div class="kv" v-for="b in isoBands" :key="b">
        <span><i class="dot" :style="{ background: isoColor(b) }"></i>{{ b }} 分钟可达</span>
        <b :style="areaOf(b) === '未生成' ? 'color:#ff9d9d' : ''">{{ areaOf(b) }}</b>
      </div>
      <p v-if="result.meta?.note" class="note" style="opacity:.75">⚠ {{ result.meta.note }}</p>
    </div>

    <!-- 洪水 -->
    <div v-else-if="result.kind === 'flood'">
      <div class="kv"><span>水位</span><b>{{ result.meta.water_level }} m</b></div>
      <div class="kv"><span>淹没面积</span><b>{{ result.meta.flooded_area_km2 }} km²</b></div>
      <div class="kv"><span>淹没单元 / 分辨率</span><b>{{ result.meta.flooded_cells }} / {{ result.meta.resolution }}²</b></div>
      <p class="note">蓝色区域为淹没范围，可与撤离路径联动避让。</p>
    </div>

    <!-- 视域 -->
    <div v-else-if="result.kind === 'viewshed'">
      <div class="kv"><span>可视比例</span><b>{{ (result.meta.ratio*100).toFixed(0) }}%</b></div>
      <div class="kv"><span>可视 / 遮挡</span><b>{{ result.meta.visible }} / {{ result.meta.blocked }}</b></div>
      <div class="legend-list">
        <span><i class="dot" style="background:#7cf6c8"></i>可视</span>
        <span><i class="dot" style="background:#ff5c7c"></i>被遮挡</span>
      </div>
    </div>

    <!-- 天际线 -->
    <div v-else-if="result.kind === 'skyline'">
      <div class="kv"><span>采样数</span><b>{{ result.meta.samples }}</b></div>
      <div class="kv"><span>最高仰角</span><b>{{ result.meta.maxElevation?.toFixed ? result.meta.maxElevation.toFixed(1) : result.meta.maxElevation }}°</b></div>
      <div class="kv"><span>最低仰角</span><b>{{ result.meta.minElevation?.toFixed ? result.meta.minElevation.toFixed(1) : result.meta.minElevation }}°</b></div>
      <div class="kv"><span>平均仰角</span><b>{{ result.meta.avgElevation }}°</b></div>
      <p class="note">金色虚线为从观察点计算出的建筑 / 地形天际线轮廓。</p>
    </div>
  </aside>
</template>

<script>
export default {
  name: 'ResultPanel',
  props: { result: Object },
  emits: ['highlight'],
  data() {
    return {
      activeAlt: 0,
      titleMap: {
        value: '地段价值评估', site: '选址分析', hotspot: '热点 / 空间自相关',
        route: '通勤路径', evacuate: '灾害撤离', iso: '服务区 / 等时圈',
        flood: '洪水淹没模拟', viewshed: '视域分析', skyline: '天际线分析'
      }
    }
  },
  watch: {
    result() { this.activeAlt = 0 }     // 每次新结果重置高亮到第一条
  },
  computed: {
    // 等时圈按"请求的"档位展示（meta.bands），缺档显示"未生成"，避免只按返回 features 掩盖丢档
    isoBands() {
      const m = this.result?.meta || {}
      const bands = (m.bands && m.bands.length) ? m.bands : (m.summary || []).map(s => s.minutes)
      return [...new Set(bands)].sort((a, b) => a - b)
    },
    isoProvider() {
      const p = String(this.result?.meta?.provider || this.result?.meta?.source || 'offline')
      if (p === 'baidu') return '百度实时路况校准·本地路网成面'
      if (p.includes('海外')) return '离线估算（海外回退）'
      if (p.includes('offline_fallback') || p.includes('回退')) return '离线补全（百度不可用）'
      return '离线路网估算'
    }
  },
  methods: {
    // 热点统计对象中文名（与 main 的热点模块一致）
    hotspotAttrLabel(attr) {
      const labels = {
        score: '综合价值',
        scenic: '景点邻近度',
        commercial: '商业区邻近度',
        school: '学校邻近度',
        hospital: '医院邻近度',
        transit: '公交邻近度',
        road: '道路邻近度'
      }
      return labels[attr] || attr || '综合价值'
    },
    // 与地图等时圈配色保持一致：按请求档位从小到大取色（绿→红）
    isoColor(minutes) {
      const ramp = ['#2ecc71', '#a3e635', '#f1c40f', '#e67e22', '#e74c3c', '#c0392b']
      return ramp[Math.min(Math.max(this.isoBands.indexOf(minutes), 0), ramp.length - 1)]
    },
    areaOf(b) {                                        // 该档面积；未返回则"未生成"
      const s = (this.result?.meta?.summary || []).find(x => x.minutes === b)
      return s ? `${s.area_km2} km²` : '未生成'
    },
    // 与 layers.js renderRoute 的备选配色一致，便于面板与地图对应
    altColor(i) { return ['#ff2d95', '#00e0ff', '#ffd400', '#9b5cff'][i % 4] },
    pickAlt(i) { this.activeAlt = i; this.$emit('highlight', i) }
  }
}
</script>

<style scoped>
.result {
  position: absolute; top: 84px; right: 14px; width: 250px; z-index: 10; padding: 16px;
}
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.head h3 { font-size: 14px; }
.kv { display: flex; justify-content: space-between; font-size: 13px; padding: 5px 0; color: var(--text-dim); }
.kv b { color: var(--text); }
.note { font-size: 12px; color: var(--text-dim); line-height: 1.6; margin-top: 8px; }
.legend { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 11px; color: var(--text-dim); }
.bar { flex: 1; height: 10px; border-radius: 6px; }
.value-bar { background: linear-gradient(90deg, #285ac8, #28b4c8, #78dc78, #f0c846, #f0465a); }
.iso-bar { background: linear-gradient(90deg, rgba(77,163,255,.9), rgba(77,163,255,.2)); }
.legend-list { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.alt-row { cursor: pointer; border-radius: 6px; padding: 5px 6px; margin: 2px 0; transition: background .12s; }
.alt-row:hover { background: rgba(255,255,255,.07); }
.alt-row.active { background: rgba(77,163,255,.20); }
</style>
