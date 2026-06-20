<template>
  <aside class="panel glass">
    <nav class="nav">
      <button v-for="m in modules" :key="m.key" class="nav-btn"
        :class="{ active: active === m.key }" @click="active = m.key">
        <span class="ico">{{ m.icon }}</span><span>{{ m.label }}</span>
      </button>
    </nav>
    <div class="divider"></div>

    <div class="body">
      <!-- 标题 + 说明 -->
      <h4 class="m-title">{{ current.icon }} {{ current.label }}</h4>
      <p class="desc">{{ current.desc }}</p>

            <!-- 底图 / 白模 -->
      <section v-if="active === 'buildings'">
        <div class="seg">
          <button class="btn sm" @click="$emit('toggle-poi')">🅿 POI 显隐</button>
          <button class="btn sm" @click="$emit('toggle-height')">🏙 高度着色</button>
        </div>
        <div class="seg">
          <button class="btn sm" :class="{ active: measureOn }" @click="$emit('toggle-measure')">📏 测距</button>
          <button class="btn sm" :class="{ active: highlightOn }" @click="$emit('toggle-highlight')">🔍 建筑高亮</button>
        </div>
        <label class="sub">相机预设</label>
        <div class="seg">
          <button class="btn sm" @click="$emit('camera-preset', 'topDown')">⬆ 俯瞰</button>
          <button class="btn sm" @click="$emit('camera-preset', 'birdEye')">🦅 鸟瞰</button>
          <button class="btn sm" @click="$emit('camera-preset', 'streetLevel')">🏠 街景</button>
        </div>
        <div class="param">
          <label>日照时刻：<b>{{ hour.toFixed(1) }}:00</b></label>
          <input type="range" min="0" max="24" step="0.1" v-model.number="hour" @input="emitSun" />
          <label class="chk"><input type="checkbox" v-model="darkNight" @change="emitSun" /> 夜晚加深（更真实的昼夜）</label>
          <label class="chk"><input type="checkbox" v-model="shadows" @change="$emit('shadows', shadows)" /> 显示日照阴影（建筑投影）</label>
          <label class="chk"><input type="checkbox" :checked="terrainOn" @change="$emit('terrain', $event.target.checked)" /> 显示三维地形起伏（街道底图建议关闭）</label>
        </div>
        <p class="tip">提示：拖动滑块连续观察光照变化；相机预设快速切换视点；测距工具左键加点、双击完成。</p>
      </section>

<!-- 价值评估 -->
      <section v-if="active === 'value'">
        <div class="weights">
          <label class="sub">各类设施权重系数（影响地段价值，可调）</label>
          <div v-for="(cn, key) in poiCn" :key="key" class="wrow">
            <span class="wname">{{ cn }}</span>
            <input type="range" min="0" max="0.4" step="0.01" v-model.number="weights[key]" />
            <span class="wval">{{ weights[key].toFixed(2) }}</span>
          </div>
          <button class="btn ghost sm" @click="resetWeights">↺ 恢复默认权重</button>
        </div>
        <div class="param">
          <label>网格分辨率：<b>{{ valueRes }} × {{ valueRes }}</b>（越大越精细、计算越慢）</label>
          <input type="range" min="24" max="96" step="4" v-model.number="valueRes" />
        </div>
        <button class="btn primary" @click="emitRun('runValue', { weights: { ...weights }, resolution: valueRes })">▶ 运行价值评估</button>
        <p class="tip">原理：对六类设施做距离衰减的"邻近度"，按上面权重加权叠加，归一化为 0–100 的地段价值。
          衰减尺度随研究区大小自适应，避免大范围下整片偏低。</p>
      </section>

      <!-- 选址 -->
      <section v-if="active === 'site'">
        <div class="param">
          <label>重点设施（侧重看重哪类邻近度）</label>
          <select class="select" v-model="siteFocus">
            <option value="">综合（默认权重）</option>
            <option v-for="(cn, key) in poiCn" :key="key" :value="key">{{ cn }}</option>
          </select>
          <label>价值阈值：<b>{{ minScore }}</b>（仅保留 ≥ 此分的地块）</label>
          <input type="range" min="40" max="95" step="1" v-model.number="minScore" />
          <label>网格分辨率：<b>{{ siteRes }} × {{ siteRes }}</b>（越大越精细、计算越慢）</label>
          <input type="range" min="24" max="96" step="4" v-model.number="siteRes" />
          <label>仅取分数最高的 K 个（留空=全部）</label>
          <input type="text" v-model="topK" placeholder="如 10" />
        </div>
        <button class="btn primary" @click="emitRun('runSite', { minScore, topK: topK ? +topK : null, weights: siteWeights, resolution: siteRes })">▶ 运行选址</button>
        <p class="tip">选址=价值评估的反向筛选。选「重点设施」可按类型侧重，如学区房选「学校」、商铺选「商业区」。</p>
      </section>

      <!-- 路径规划 -->
      <section v-if="active === 'route'">
        <div class="pickrow">
          <button class="btn sm" :class="{ active: pendingPick === 'route-start' }" @click="$emit('pick', 'route-start')">① 选起点</button>
          <span class="coord">{{ fmt(picks.routeStart) }}</span>
        </div>
        <div class="pickrow">
          <button class="btn sm" :class="{ active: pendingPick === 'route-end' }" @click="$emit('pick', 'route-end')">② 选终点</button>
          <span class="coord">{{ fmt(picks.routeEnd) }}</span>
        </div>
        <div class="pickrow">
          <button class="btn sm" :class="{ active: pendingPick === 'route-via' }" @click="$emit('pick', 'route-via')">＋ 途径点</button>
          <span class="coord">{{ picks.routeVias && picks.routeVias.length ? picks.routeVias.length + ' 个' : '无' }}</span>
          <button v-if="picks.routeVias && picks.routeVias.length" class="btn sm ghost" @click="$emit('clear-vias')">清空</button>
        </div>
        <label class="sub">交通方式</label>
        <select class="select" v-model="routeMode">
          <option v-for="m in modes" :key="m.v" :value="m.v">{{ m.t }}</option>
        </select>
        <div class="seg">
          <button class="btn sm" :class="{ active: optimize === 'time' }" @click="optimize = 'time'">最快</button>
          <button class="btn sm" :class="{ active: optimize === 'length' }" @click="optimize = 'length'">最短</button>
        </div>
        <button class="btn primary" :disabled="!picks.routeStart || !picks.routeEnd"
          @click="emitRun('runRoute', { optimize, mode: routeMode })">▶ 通勤路径</button>
        <p v-if="!picks.routeStart || !picks.routeEnd" class="hint-inline">请先选起点和终点</p>

        <div class="divider"></div>
        <label class="sub">🚨 灾害撤离</label>
        <div class="pickrow">
          <button class="btn sm" :class="{ active: pendingPick === 'evac-start' }" @click="$emit('pick', 'evac-start')">选撤离起点</button>
          <span class="coord">{{ fmt(picks.evacStart) }}</span>
        </div>
        <button class="btn danger" :disabled="!picks.evacStart" @click="emitRun('runEvacuate', { mode: routeMode })">▶ 撤离到最近避难场所</button>
        <p class="tip">基于路网（默认内置样例；运行 data/fetch_data.py 后用真实 OSM）。可选交通方式与途径点；
          撤离自动就近选避难场所，若先做洪水模拟会避开淹没区。</p>
      </section>

      <!-- 热点分析 -->
      <section v-if="active === 'hotspot'">
        <div class="param">
          <label>统计对象</label>
          <select class="select" v-model="hotspotAttr">
            <option value="score">综合价值</option>
            <option v-for="(cn, key) in poiCn" :key="key" :value="key">{{ cn }}邻近度</option>
          </select>
          <label>网格分辨率：<b>{{ hotspotRes }} × {{ hotspotRes }}</b>（越大越精细、计算越慢）</label>
          <input type="range" min="24" max="96" step="4" v-model.number="hotspotRes" />
          <label>邻居数 K：<b>{{ hotspotK }}</b></label>
          <input type="range" min="4" max="24" step="2" v-model.number="hotspotK" />
        </div>
        <button class="btn primary" @click="emitRun('runHotspot', { weights: { ...weights }, resolution: hotspotRes, attr: hotspotAttr, k: hotspotK })">▶ 运行热点分析</button>
        <p class="tip">可分析综合价值，也可单独看学校、医院、商业区等设施邻近度的空间聚集。</p>
      </section>

      <!-- 服务区 -->
      <section v-if="active === 'iso'">
        <div class="pickrow">
          <button class="btn sm" :class="{ active: pendingPick === 'iso-center' }" @click="$emit('pick', 'iso-center')">选中心点</button>
          <span class="coord">{{ fmt(picks.isoCenter) }}</span>
        </div>
        <label class="sub">交通方式</label>
        <select class="select" v-model="isoMode">
          <option v-for="m in modes" :key="m.v" :value="m.v">{{ m.t }}</option>
        </select>
        <label class="sub">时间档（分钟）</label>
        <div class="seg">
          <label v-for="b in [5,10,15,20,30]" :key="b" class="chk">
            <input type="checkbox" :value="b" v-model="bands" /> {{ b }}
          </label>
        </div>
        <button class="btn primary" :disabled="!picks.isoCenter || !bands.length"
          @click="emitRun('runServiceArea', { bands: [...bands].sort((a,b)=>a-b), mode: isoMode })">▶ 生成等时圈</button>
        <p v-if="!picks.isoCenter" class="hint-inline">请先选设施中心点</p>
        <p class="tip">沿路网计算从中心点出发 N 分钟可达范围，常用于设施服务能力评估。各档用绿→红分明配色。</p>
      </section>

      <!-- 洪水淹没 -->
      <section v-if="active === 'flood'">
        <div class="param">
          <label>水位：<b>{{ waterLevel }} m</b></label>
          <input type="range" min="1" max="20" step="0.5" v-model.number="waterLevel" />
          <label>淹没分辨率：<b>{{ floodRes }} × {{ floodRes }}</b>（越大越精细、计算越慢）</label>
          <input type="range" min="60" max="140" step="10" v-model.number="floodRes" />
          <label class="chk"><input type="checkbox" v-model="reroute" /> 与撤离联动（避开淹没区重算路线）</label>
        </div>
        <button class="btn primary" @click="emitRun('runFlood', { waterLevel, reroute, resolution: floodRes })">▶ 模拟淹没（当前水位）</button>
        <button class="btn" @click="emitRun('runFloodAnim', { waterLevel, resolution: floodRes })">▶ 涨水过程动画（0→当前水位）</button>
        <p class="tip">按高程从水系（河/湖/海岸）连通漫淹：水位=洪水位/海平面，高地挡水。
          「涨水过程」按帧播放水位逐渐升高的淹没扩张。默认内置样例水系，运行 fetch_data.py 后用真实水系。</p>
      </section>

            <!-- 视域分析 -->
      <section v-if="active === 'viewshed'">
        <div class="pickrow">
          <button class="btn sm" :class="{ active: pendingPick === 'observer' }" @click="$emit('pick', 'observer')">👁 选观察点</button>
          <span class="coord">{{ fmt(picks.observer) }}</span>
        </div>
        <div class="param">
          <label>观察高度（离地）：<b>{{ eyeHeight }} m</b></label>
          <input type="range" min="5" max="200" step="1" v-model.number="eyeHeight" />
          <label>分析半径：<b>{{ viewRadius }} m</b></label>
          <input type="range" min="200" max="3000" step="50" v-model.number="viewRadius" />
          <label>方位采样数：<b>{{ azimuthSamples }}</b></label>
          <input type="range" min="24" max="192" step="12" v-model.number="azimuthSamples" />
          <label class="chk"><input type="checkbox" v-model="showArea" /> 显示视域覆盖区域面</label>
        </div>
        <button class="btn primary" :disabled="!picks.observer" @click="emitRun('runViewshed', { eyeHeight, radius: viewRadius, azimuths: azimuthSamples, showArea })">▶ 运行视域分析</button>
        <p v-if="!picks.observer" class="hint-inline">请先选观察点</p>
        <div class="divider" style="margin: 8px 0"></div>
        <div class="seg">
          <button class="btn sm" :disabled="!picks.observer" @click="emitRun('runSkyline', { eyeHeight })">🌇 天际线分析</button>
          <button class="btn sm" :disabled="!picks.observer" @click="emitRun('runViewshed', { eyeHeight, radius: viewRadius, azimuths: azimuthSamples, showArea })">🔄 更新视域</button>
        </div>
        <p class="tip">从观察点按方位×俯仰角发射射线，检测建筑地形遮挡。绿=可视、红=被挡。天际线显示水平方向建筑轮廓。</p>
      </section>

    </div>

    <div class="footer">
      <button class="btn ghost sm" @click="$emit('clear')">🗑 清空图层</button>
      <span v-if="busy" class="busy"><i class="spinner"></i> 计算中</span>
    </div>
  </aside>
</template>

<script>
const DEFAULT_WEIGHTS = { scenic: 0.15, commercial: 0.25, school: 0.20, hospital: 0.15, transit: 0.20, road: 0.05 }

export default {
  name: 'ControlPanel',
  props: { poiCn: Object, picks: Object, busy: Boolean, pendingPick: String, terrainOn: Boolean },
  emits: ['run', 'pick', 'toggle-poi', 'toggle-height', 'sun', 'shadows', 'terrain', 'clear', 'clear-vias'],
  data() {
    return {
      active: 'buildings',
      hour: 9, darkNight: true, shadows: true,
      weights: { ...DEFAULT_WEIGHTS },
      minScore: 70, topK: '', optimize: 'time', siteFocus: '',
      bands: [5, 10, 15], waterLevel: 6, reroute: true, valueRes: 48, siteRes: 48, hotspotRes: 48, hotspotK: 8, hotspotAttr: 'score', floodRes: 100,
      eyeHeight: 30, radius: 600,
      routeMode: 'drive', isoMode: 'drive',
      modes: [{ v: 'drive', t: '🚗 驾车' }, { v: 'cycle', t: '🚲 骑行' }, { v: 'walk', t: '🚶 步行' }, { v: 'transit', t: '🚌 公交' }],
      modules: [
        { key: 'buildings', icon: '🏢', label: '白模底座', desc: '城市白模底座（Cesium OSM Buildings 全球数据），可切换底图、查看建筑高度与日照阴影。' },
        { key: 'value', icon: '💰', label: '价值评估', desc: '多因子缓冲 + 距离衰减 + 加权叠加，生成三维地段价值热力。权重系数可调。' },
        { key: 'site', icon: '📍', label: '选址分析', desc: '在价值评估基础上筛选高分地块，用于商业 / 住宅 / 设施选址。' },
        { key: 'route', icon: '🧭', label: '路径规划', desc: '基于真实路网的网络分析：通勤最优路径与灾害撤离路径。' },
        { key: 'hotspot', icon: '🔥', label: '热点分析', desc: '空间自相关（Moran\'s I）+ 热点（Getis-Ord Gi*），识别价值聚集格局。' },
        { key: 'iso', icon: '⏱', label: '服务区', desc: '从设施点出发 N 分钟可达范围（等时圈），评估服务覆盖能力。' },
        { key: 'flood', icon: '🌊', label: '洪水淹没', desc: '从真实水系起淹、按地形连通扩散的洪水模拟，可与撤离联动。' },
        { key: 'viewshed', icon: '👁', label: '视域分析', desc: '考虑建筑高度的真三维视域 / 通视分析 + 天际线轮廓。' }
      ]
    }
  },
  computed: {
    current() { return this.modules.find(m => m.key === this.active) },
    // 选址权重：选了“重点设施”则提升该类权重，实现按设施类型侧重选址
    siteWeights() {
      const w = { ...this.weights }
      if (this.siteFocus) w[this.siteFocus] = Math.max(0.5, w[this.siteFocus] || 0)
      return w
    }
  },
  methods: {
    emitRun(fn, payload) { this.$emit('run', { fn, payload }) },
    emitSun() { this.$emit('sun', { hour: this.hour, darkNight: this.darkNight }) },
    resetWeights() { this.weights = { ...DEFAULT_WEIGHTS } },
    fmt(p) { return p ? `${p[0]}, ${p[1]}` : '未选取' }
  }
}
</script>

<style scoped>
.panel {
  position: absolute; top: 84px; left: 14px; width: 300px;
  max-height: calc(100% - 120px); z-index: 10; display: flex; flex-direction: column; padding: 14px;
}
.nav { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.nav-btn {
  display: flex; align-items: center; gap: 6px; padding: 8px 10px; border-radius: 10px;
  border: 1px solid transparent; cursor: pointer; background: rgba(255,255,255,.03);
  color: var(--text-dim); font-size: 12px; transition: all .15s;
}
.nav-btn:hover { color: var(--text); background: rgba(77,163,255,.1); }
.nav-btn.active { color: var(--text); background: rgba(77,163,255,.18); border-color: var(--border); }
.ico { font-size: 14px; }
.divider { height: 1px; background: var(--border); margin: 12px 0; }
.body { overflow-y: auto; padding-right: 4px; }
.m-title { font-size: 14px; margin-bottom: 6px; }
.desc { font-size: 12px; color: var(--text-dim); line-height: 1.6; margin-bottom: 12px; }
.tip { font-size: 11px; color: var(--text-dim); line-height: 1.6; margin-top: 10px;
  padding: 8px 10px; background: rgba(77,163,255,.06); border-radius: 8px; border-left: 2px solid var(--accent); }
.sub { font-size: 12px; color: var(--accent-2); display: block; margin: 8px 0 6px; }
.btn { width: 100%; justify-content: center; margin-top: 8px; }
.btn.primary { background: var(--accent); color: #06121f; font-weight: 600; border-color: var(--accent); }
.btn.primary:hover { filter: brightness(1.08); }
.btn:disabled { opacity: .45; cursor: not-allowed; filter: none; }
.select { width: 100%; margin-top: 6px; padding: 7px 9px; border-radius: 8px;
  background: #16213a; color: #e8eef8; border: 1px solid var(--border); font-size: 12px; }
.select:focus { outline: none; border-color: var(--accent); }
/* 展开的下拉项：显式深底浅字，避免某些系统下灰底灰字看不清 */
.select option { background: #16213a; color: #e8eef8; }
.hint-inline { font-size: 11px; color: var(--accent-2); margin-top: 6px; text-align: center; }
.btn.danger { background: var(--danger); color: #1a0610; font-weight: 600; border-color: var(--danger); }
.param { margin-top: 8px; }
.param label { display: block; margin-top: 10px; }
.pickrow { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.pickrow .btn { width: auto; margin: 0; white-space: nowrap; }
.coord { font-size: 11px; color: var(--text-dim); font-family: monospace; }
.seg { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.seg .btn { width: auto; margin: 0; }
.chk { font-size: 12px; color: var(--text); display: inline-flex; align-items: center; gap: 4px; margin-top: 8px; }
.weights { margin-bottom: 6px; }
.wrow { display: grid; grid-template-columns: 56px 1fr 34px; align-items: center; gap: 8px; margin-top: 6px; }
.wname { font-size: 12px; color: var(--text-dim); }
.wval { font-size: 11px; font-family: monospace; color: var(--accent-2); text-align: right; }
.footer { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
.busy { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--accent); }
</style>



