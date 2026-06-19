/**
 * 图层渲染：把后端返回的 GeoJSON 结果绘制到 Cesium 场景。
 * 每类结果用一个具名 CustomDataSource 管理，便于独立显隐与清除。
 */
import * as Cesium from 'cesium'

/** 分数 → 热力色（蓝→青→黄→橙→红）。t ∈ [0,1] */
function heatColor(t, alpha = 0.65) {
  const stops = [
    [0.0, [40, 90, 200]], [0.25, [40, 180, 200]], [0.5, [120, 220, 120]],
    [0.75, [240, 200, 70]], [1.0, [240, 70, 90]]
  ]
  let c = stops[stops.length - 1][1]
  for (let i = 0; i < stops.length - 1; i++) {
    if (t <= stops[i + 1][0]) {
      const [t0, a] = stops[i], [t1, b] = stops[i + 1]
      const f = (t - t0) / (t1 - t0 || 1)
      c = a.map((v, k) => Math.round(v + (b[k] - v) * f))
      break
    }
  }
  return Cesium.Color.fromBytes(c[0], c[1], c[2], Math.round(alpha * 255))
}

// POI 类别 → 图标字符（Unicode 符号，用于 Billboard）
const POI_ICONS = {
  scenic: '🏔', commercial: '🏪', school: '🏫',
  hospital: '🏥', transit: '🚌', road: '🛣'
}

// POI 类别 → 颜色
const POI_COLORS = {
  scenic: '#7cf6c8', commercial: '#ff9d5c', school: '#ffe07c',
  hospital: '#ff5c7c', transit: '#4da3ff', road: '#9aa7c7'
}

/** 图层管理器：统一增删具名图层。*/
export class LayerManager {
  constructor(viewer) {
    this.viewer = viewer
    this.sources = new Map()
  }
  _ds(name) {
    if (!this.sources.has(name)) {
      const ds = new Cesium.CustomDataSource(name)
      this.viewer.dataSources.add(ds)
      this.sources.set(name, ds)
    }
    return this.sources.get(name)
  }
  clear(name) { if (this.sources.has(name)) this.sources.get(name).entities.removeAll() }
  setVisible(name, v) { if (this.sources.has(name)) this.sources.get(name).show = v }
  clearAll() { this.sources.forEach(ds => ds.entities.removeAll()) }

  /** 价值评估 / 选址：网格按分数热度着色，并按分数轻微抬升形成三维价值面。*/
  renderValueGrid(fc, name = 'value') {
    this.clear(name)
    const ds = this._ds(name)
    for (const f of fc.features) {
      const score = f.properties.score
      const ring = f.geometry.coordinates[0].flat()
      ds.entities.add({
        polygon: {
          hierarchy: Cesium.Cartesian3.fromDegreesArray(ring),
          material: heatColor(score / 100, 0.6),
          extrudedHeight: 20 + score * 3,
          height: 0,
          outline: false
        },
        properties: { score, detail: f.properties.detail }
      })
    }
  }

  /** 热点分析：按 hot/cold/none 着色（红=热点，蓝=冷点，灰=不显著）。*/
  renderHotspot(fc, name = 'hotspot') {
    this.clear(name)
    const ds = this._ds(name)
    const colorOf = (c) => c === 'hot' ? Cesium.Color.fromBytes(240, 70, 90, 170)
      : c === 'cold' ? Cesium.Color.fromBytes(70, 130, 240, 170)
        : Cesium.Color.fromBytes(140, 150, 170, 70)
    for (const f of fc.features) {
      const ring = f.geometry.coordinates[0].flat()
      ds.entities.add({
        polygon: {
          hierarchy: Cesium.Cartesian3.fromDegreesArray(ring),
          material: colorOf(f.properties.hot_class),
          classificationType: Cesium.ClassificationType.TERRAIN
        },
        properties: { gi_z: f.properties.gi_z, hot_class: f.properties.hot_class }
      })
    }
  }

  /**
   * POI 点：按类别用不同颜色的圆点 + 图标标签 + 可点击。
   * @param {Object} fc       GeoJSON FeatureCollection
   * @param {Object} poiCn    POI 类别中文名映射
   * @param {string} name     图层名（默认 'pois'）
   */
  renderPois(fc, poiCn, name = 'pois') {
    this.clear(name)
    const ds = this._ds(name)
    for (const f of fc.features) {
      const cat = f.properties.category
      const [lon, lat] = f.geometry.coordinates
      const colorHex = POI_COLORS[cat] || '#fff'
      const icon = POI_ICONS[cat] || '📍'
      const cnLabel = (poiCn || {})[cat] || cat

      // 圆点标记
      ds.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
        point: {
          pixelSize: 9, color: Cesium.Color.fromCssColorString(colorHex),
          outlineColor: Cesium.Color.BLACK, outlineWidth: 1,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        },
        properties: { category: cat, name: f.properties.name, cn: cnLabel }
      })

      // 图标标签
      ds.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat, 5),
        label: {
          text: `${icon} ${f.properties.name || cnLabel}`,
          font: '11px sans-serif',
          fillColor: Cesium.Color.WHITE,
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString(colorHex).withAlpha(0.75),
          backgroundPadding: new Cesium.Cartesian2(5, 3),
          pixelOffset: new Cesium.Cartesian2(12, -6),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }
      })
    }
  }

  /** 路径（通勤 / 撤离）：发光折线 + 端点。*/
  renderRoute(fc, name = 'route', color = '#4da3ff') {
    this.clear(name)
    const ds = this._ds(name)
    const c = Cesium.Color.fromCssColorString(color)
    for (const f of fc.features) {
      if (f.geometry.type === 'LineString') {
        const coords = f.geometry.coordinates.flatMap(c => [c[0], c[1]])
        ds.entities.add({
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArray(coords),
            width: 4, material: c.withAlpha(0.8),
            clampToGround: true
          }
        })
        // 外发光层
        ds.entities.add({
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArray(coords),
            width: 9, material: c.withAlpha(0.22),
            clampToGround: true
          }
        })
        // 起点
        if (coords.length >= 2) {
          ds.entities.add({
            position: Cesium.Cartesian3.fromDegrees(coords[0], coords[1], 0),
            point: { pixelSize: 10, color: Cesium.Color.WHITE, outlineColor: c, outlineWidth: 3,
              heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
              disableDepthTestDistance: Number.POSITIVE_INFINITY }
          })
        }
        // 终点
        if (coords.length >= 4) {
          const n = coords.length
          ds.entities.add({
            position: Cesium.Cartesian3.fromDegrees(coords[n - 2], coords[n - 1], 0),
            point: { pixelSize: 10, color: c, outlineColor: Cesium.Color.WHITE, outlineWidth: 2,
              heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
              disableDepthTestDistance: Number.POSITIVE_INFINITY },
            label: {
              text: '终点', font: '12px sans-serif',
              fillColor: Cesium.Color.WHITE,
              showBackground: true, backgroundColor: c.withAlpha(0.85),
              backgroundPadding: new Cesium.Cartesian2(6, 3),
              pixelOffset: new Cesium.Cartesian2(0, -16),
              heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
              disableDepthTestDistance: Number.POSITIVE_INFINITY
            }
          })
        }
      }
      // 避难场所点（用于撤离路径）
      if (f.geometry.type === 'Point') {
        const [lon, lat] = f.geometry.coordinates
        const isShelter = f.properties.kind === 'shelter'
        ds.entities.add({
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          point: { pixelSize: isShelter ? 11 : 8, color: isShelter ? Cesium.Color.fromBytes(120, 246, 200) : c,
            outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY },
          label: isShelter ? {
            text: '🛖 ' + (f.properties.name || '避难场所'),
            font: '13px sans-serif', fillColor: Cesium.Color.WHITE,
            showBackground: true, backgroundColor: Cesium.Color.fromBytes(10, 16, 30, 200),
            pixelOffset: new Cesium.Cartesian2(0, -22),
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY
          } : undefined
        })
      }
    }
  }

  /** 服务区 / 等时圈：每个时间档用分明的"近→远"配色（绿→黄→橙→红），形成清晰环带。*/
  renderIsochrone(fc, name = 'iso') {
    this.clear(name)
    const ds = this._ds(name)
    const ramp = ['#2ecc71', '#a3e635', '#f1c40f', '#e67e22', '#e74c3c', '#c0392b']
    const mins = [...new Set(fc.features.map(f => f.properties.minutes))].sort((a, b) => a - b)
    const colorOf = (m) => ramp[Math.min(Math.max(mins.indexOf(m), 0), ramp.length - 1)]
    const feats = [...fc.features].sort((a, b) => b.properties.minutes - a.properties.minutes)
    for (const f of feats) {
      const m = f.properties.minutes
      const ring = f.geometry.coordinates[0].flat()
      const c = Cesium.Color.fromCssColorString(colorOf(m))
      ds.entities.add({
        polygon: {
          hierarchy: Cesium.Cartesian3.fromDegreesArray(ring),
          material: c.withAlpha(0.5),
          outline: true, outlineColor: c.withAlpha(0.95), outlineWidth: 2,
          classificationType: Cesium.ClassificationType.TERRAIN
        },
        properties: { minutes: m }
      })
    }
  }

  /** 洪水淹没：单一平滑水面，贴地铺设（淹没"footprint"），地形开/关都正确对齐。*/
  renderFlood(result, name = 'flood') {
    this.clear(name)
    const ds = this._ds(name)
    const level = result.meta.water_level
    const feats = result.surface?.features || []
    for (const f of feats) {
      for (const ring of ringsOf(f.geometry)) {
        ds.entities.add({
          polygon: {
            hierarchy: Cesium.Cartesian3.fromDegreesArray(ring.flat()),
            material: Cesium.Color.fromBytes(40, 130, 220, 150),
            classificationType: Cesium.ClassificationType.TERRAIN,
            outline: true, outlineColor: Cesium.Color.fromBytes(120, 200, 255, 220)
          },
          properties: { water_level: level }
        })
      }
    }
  }

  // ═══════════════════════════════════════════════════════════
  //  标记系统
  // ═══════════════════════════════════════════════════════════

  /**
   * 在地图上放置醒目的选点标记（立柱 + 顶点 + 标签）。
   * 同一 key 的旧标记自动移除。
   *
   * @param {string} key       唯一标识
   * @param {[number,number]} lonlat 经纬度
   * @param {string} label     标签文字
   * @param {string} color     颜色（CSS），默认 '#ffd24d'
   * @param {Object} opts      可选配置
   * @param {number} opts.height 标记高度（米），默认 120
   * @param {number} opts.pixelSize 顶点大小，默认 9
   */
  marker(key, lonlat, label, color = '#ffd24d', opts = {}) {
    const { height = 120, pixelSize = 9 } = opts
    const ds = this._ds('picks')
    // 移除同 key 旧标记
    ds.entities.values.filter(e => e.properties?.key?.getValue() === key)
      .forEach(e => ds.entities.remove(e))
    const [lon, lat] = lonlat
    const c = Cesium.Color.fromCssColorString(color)

    // 立柱（锥形：底部宽→顶部窄）
    ds.entities.add({
      position: Cesium.Cartesian3.fromDegrees(lon, lat, height / 2),
      cylinder: { length: height, topRadius: 1, bottomRadius: 10, material: c.withAlpha(0.7),
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND },
      properties: { key }
    })
    // 顶点
    ds.entities.add({
      position: Cesium.Cartesian3.fromDegrees(lon, lat, height + 5),
      point: { pixelSize, color: c, outlineColor: Cesium.Color.BLACK, outlineWidth: 1,
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY },
      label: { text: label, font: 'bold 12px sans-serif', fillColor: Cesium.Color.WHITE,
        showBackground: true, backgroundColor: c.withAlpha(0.9),
        backgroundPadding: new Cesium.Cartesian2(7, 4),
        pixelOffset: new Cesium.Cartesian2(0, -14),
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY },
      properties: { key }
    })
  }

  /** 清除指定 key 的标记。 */
  clearMarker(key) {
    const ds = this._ds('picks')
    ds.entities.values.filter(e => e.properties?.key?.getValue() === key)
      .forEach(e => ds.entities.remove(e))
  }

  /** 清除所有标记。 */
  clearAllMarkers() {
    this.clear('picks')
  }

  // ═══════════════════════════════════════════════════════════
  //  视域/天际线辅助图层
  // ═══════════════════════════════════════════════════════════

  /**
   * 渲染视域覆盖区域（半透明面）。
   * @param {Array<{lon:number,lat:number}>} ring 边界点（按方位角排序）
   * @param {string} name 图层名
   */
  renderCoverageArea(ring, name = 'coverage') {
    this.clear(name)
    const ds = this._ds(name)
    if (!ring || ring.length < 3) return
    const flat = ring.flatMap(p => [p.lon, p.lat])
    // 闭合
    flat.push(flat[0], flat[1])
    ds.entities.add({
      polygon: {
        hierarchy: Cesium.Cartesian3.fromDegreesArray(flat),
        material: Cesium.Color.fromBytes(120, 246, 200, 50),
        outline: true,
        outlineColor: Cesium.Color.fromBytes(120, 246, 200, 160),
        outlineWidth: 1.5,
        classificationType: Cesium.ClassificationType.TERRAIN
      }
    })
  }

  /**
   * 清除指定图层并渲染折线（通用工具）。
   */
  renderPolyline(name, positions, color, width = 2, dashed = false) {
    this.clear(name)
    const ds = this._ds(name)
    if (!positions || positions.length < 2) return
    const c = Cesium.Color.fromCssColorString(color)
    const material = dashed
      ? new Cesium.PolylineDashMaterialProperty({ color: c.withAlpha(0.85), dashLength: 12 })
      : c.withAlpha(0.85)
    ds.entities.add({
      polyline: {
        positions: positions.map(p => Cesium.Cartesian3.fromDegrees(p[0], p[1], p[2] || 0)),
        width,
        material,
        clampToGround: false
      }
    })
  }
}

/** 取出 Polygon / MultiPolygon 的所有外环坐标数组。*/
function ringsOf(geom) {
  if (!geom) return []
  if (geom.type === 'Polygon') return [geom.coordinates[0]]
  if (geom.type === 'MultiPolygon') return geom.coordinates.map(p => p[0])
  return []
}
