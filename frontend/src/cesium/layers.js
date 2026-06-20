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

/** 图层管理器：统一增删具名图层。 */
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

  /** 价值评估 / 选址：网格按分数热力着色，并按分数轻微抬升形成三维价值面。 */
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

  /** 热点分析：按 hot/cold/none 着色（红=热点，蓝=冷点，灰=不显著）。 */
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

  /** POI 点：按类别用不同颜色的圆点 + 标签。 */
  renderPois(fc, poiCn, name = 'pois') {
    this.clear(name)
    const ds = this._ds(name)
    const palette = {
      scenic: '#7cf6c8', commercial: '#ff9d5c', school: '#ffe07c',
      hospital: '#ff5c7c', transit: '#4da3ff', road: '#9aa7c7'
    }
    for (const f of fc.features) {
      const cat = f.properties.category
      const [lon, lat] = f.geometry.coordinates
      ds.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
        point: {
          pixelSize: 9, color: Cesium.Color.fromCssColorString(palette[cat] || '#fff'),
          outlineColor: Cesium.Color.BLACK, outlineWidth: 1,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        },
        properties: { category: cat, name: f.properties.name, cn: (poiCn || {})[cat] }
      })
    }
  }

  /**
   * 路径（通勤 / 撤离）：贴地折线 + 端点。
   * 高对比配色 + 黑色描边，确保在影像/街道任何底图上都醒目；多条备选各用不同颜色，
   * 默认高亮第 0 条，可调用 highlightRoute(i) 切换高亮。
   */
  renderRoute(fc, name = 'route', color = '#4da3ff') {
    this.clear(name)
    const ds = this._ds(name)
    const palette = ['#ff2d95', '#00e0ff', '#ffd400', '#9b5cff']  // 洋红/青/黄/紫，避免与底图相近
    const lineCount = fc.features.filter(f => f.geometry.type === 'LineString').length
    const multi = lineCount > 1
    let li = 0
    for (const f of fc.features) {
      if (f.geometry.type === 'LineString') {
        const coords = f.geometry.coordinates.flatMap(c => [c[0], c[1]])
        const segType = f.properties?.seg_type            // 公交/地铁分段：按类型上色
        if (segType) {
          const sc = segType === 'subway' ? '#2d7dff' : segType === 'bus' ? '#ff9d2e' : '#9aa7c7'
          ds.entities.add({
            polyline: {
              positions: Cesium.Cartesian3.fromDegreesArray(coords),
              width: segType === 'walk' ? 5 : 8,
              material: segType === 'walk'
                ? new Cesium.PolylineDashMaterialProperty({ color: Cesium.Color.fromCssColorString(sc), dashLength: 16 })
                : new Cesium.PolylineOutlineMaterialProperty({
                  color: Cesium.Color.fromCssColorString(sc),
                  outlineColor: Cesium.Color.fromBytes(0, 0, 0, 210), outlineWidth: 2.5
                }),
              clampToGround: true
            },
            properties: { ...f.properties }
          })
        } else {
          // 驾车/自研路线：贴地 + 亮色黑描边；多条备选各取一色、默认高亮第一条、可切换。
          const idx = li
          const c = multi ? palette[idx % palette.length] : color
          const selected = (f.properties?.rank ?? idx) === 0
          ds.entities.add({
            polyline: {
              positions: Cesium.Cartesian3.fromDegreesArray(coords),
              width: selected ? 9 : 5,
              material: new Cesium.PolylineOutlineMaterialProperty({
                color: Cesium.Color.fromCssColorString(c).withAlpha(selected ? 1.0 : 0.6),
                outlineColor: Cesium.Color.fromBytes(0, 0, 0, 210), outlineWidth: 2.5
              }),
              clampToGround: true,
              zIndex: selected ? 10 : 1
            },
            properties: { ...f.properties, _routeIndex: idx, _color: c }
          })
          li++
        }
      } else if (f.geometry.type === 'Point') {
        const [lon, lat] = f.geometry.coordinates
        const isShelter = f.properties.type === 'shelter'
        ds.entities.add({
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          billboard: undefined,
          point: { pixelSize: 14, color: Cesium.Color.fromCssColorString(isShelter ? '#7cf6c8' : '#ffffff'),
            outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY },
          label: isShelter ? {
            text: '🛟 ' + (f.properties.name || '避难场所'),
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

  /** 高亮指定序号的备选路线（其余变细变淡），用于结果面板里点击"方案N"切换。 */
  highlightRoute(index, name = 'route') {
    const ds = this.sources.get(name)
    if (!ds) return
    for (const e of ds.entities.values) {
      if (!e.polyline || !e.properties || e.properties._routeIndex === undefined) continue
      const idx = e.properties._routeIndex.getValue()
      const c = e.properties._color.getValue()
      const sel = idx === index
      e.polyline.width = sel ? 9 : 5
      e.polyline.material = new Cesium.PolylineOutlineMaterialProperty({
        color: Cesium.Color.fromCssColorString(c).withAlpha(sel ? 1.0 : 0.5),
        outlineColor: Cesium.Color.fromBytes(0, 0, 0, 210), outlineWidth: 2.5
      })
      e.polyline.zIndex = sel ? 10 : 1
    }
  }

  /** 服务区 / 等时圈：每个时间档用分明的“近→远”配色（绿→黄→橙→红），形成清晰环带。 */
  renderIsochrone(fc, name = 'iso') {
    this.clear(name)
    const ds = this._ds(name)
    // 近(分钟少)=绿，远(分钟多)=红；按实际档位排序后取色，区分更明显
    const ramp = ['#2ecc71', '#a3e635', '#f1c40f', '#e67e22', '#e74c3c', '#c0392b']
    // 按"请求的"档位(meta.bands)建立配色刻度，与结果面板一致，避免缺档时错配颜色
    const reqBands = (fc.meta && fc.meta.bands && fc.meta.bands.length) ? fc.meta.bands : fc.features.map(f => f.properties.minutes)
    const mins = [...new Set(reqBands)].sort((a, b) => a - b)
    const colorOf = (m) => ramp[Math.min(Math.max(mins.indexOf(m), 0), ramp.length - 1)]
    // 大档先画、小档后画（叠在上层），让每一档露出独立的环
    const feats = [...fc.features].sort((a, b) => b.properties.minutes - a.properties.minutes)
    for (const f of feats) {
      const m = f.properties.minutes
      const c = Cesium.Color.fromCssColorString(colorOf(m))
      for (const poly of polysOf(f.geometry)) {        // 每个多边形：外环 + 孔洞（水面/空地）
        const outer = Cesium.Cartesian3.fromDegreesArray(poly.outer.flat())
        const holes = poly.holes.map(h => new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(h.flat())))
        ds.entities.add({
          polygon: {                                   // 贴地填充，保留孔洞（贴地多边形不支持 outline）
            hierarchy: new Cesium.PolygonHierarchy(outer, holes),
            material: c.withAlpha(0.45),
            classificationType: Cesium.ClassificationType.TERRAIN
          },
          properties: { minutes: m }
        })
        ds.entities.add({                              // 外边界贴地线，消除 outline 警告、环带清晰
          polyline: { positions: outer, width: 2, material: c.withAlpha(0.95), clampToGround: true },
          properties: { minutes: m }
        })
        for (const h of poly.holes)                    // 孔洞（如西湖）边界也描一圈
          ds.entities.add({
            polyline: { positions: Cesium.Cartesian3.fromDegreesArray(h.flat()), width: 1.5, material: c.withAlpha(0.8), clampToGround: true },
            properties: { minutes: m }
          })
      }
    }
  }

  /** 洪水淹没：单一平滑水面，贴地铺设（淹没"footprint"），地形开/关都正确对齐。 */
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
            classificationType: Cesium.ClassificationType.TERRAIN,   // 贴地，避免水面悬空/入地
            outline: true, outlineColor: Cesium.Color.fromBytes(120, 200, 255, 220)
          },
          properties: { water_level: level }
        })
      }
    }
  }

  /** 在地图上放置醒目的选点标记（立柱 + 顶点 + 标签）。 */
  marker(key, lonlat, label, color = '#ffd24d') {
    const ds = this._ds('picks')
    ds.entities.values.filter(e => e.properties?.key?.getValue() === key)
      .forEach(e => ds.entities.remove(e))
    const [lon, lat] = lonlat
    const c = Cesium.Color.fromCssColorString(color)
    ds.entities.add({
      position: Cesium.Cartesian3.fromDegrees(lon, lat, 60),
      cylinder: { length: 120, topRadius: 0, bottomRadius: 12, material: c.withAlpha(0.85),
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND },
      properties: { key }
    })
    ds.entities.add({
      position: Cesium.Cartesian3.fromDegrees(lon, lat, 130),
      point: { pixelSize: 9, color: c, outlineColor: Cesium.Color.BLACK, outlineWidth: 1,
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY },
      label: { text: label, font: 'bold 13px sans-serif', fillColor: Cesium.Color.WHITE,
        showBackground: true, backgroundColor: c.withAlpha(0.9),
        backgroundPadding: new Cesium.Cartesian2(7, 4),
        pixelOffset: new Cesium.Cartesian2(0, -14),
        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY },
      properties: { key }
    })
  }
  clearMarker(key) {
    const ds = this._ds('picks')
    ds.entities.values.filter(e => e.properties?.key?.getValue() === key)
      .forEach(e => ds.entities.remove(e))
  }
}

/** 取出 Polygon / MultiPolygon 的所有外环坐标数组。 */
function ringsOf(geom) {
  if (!geom) return []
  if (geom.type === 'Polygon') return [geom.coordinates[0]]
  if (geom.type === 'MultiPolygon') return geom.coordinates.map(p => p[0])
  return []
}

/** 取出每个多边形的外环与孔洞：{ outer:[[lon,lat]...], holes:[[[lon,lat]...]...] }。 */
function polysOf(geom) {
  if (!geom) return []
  if (geom.type === 'Polygon') return [{ outer: geom.coordinates[0], holes: geom.coordinates.slice(1) }]
  if (geom.type === 'MultiPolygon') return geom.coordinates.map(p => ({ outer: p[0], holes: p.slice(1) }))
  return []
}
