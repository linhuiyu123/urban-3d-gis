/**
 * 三维视域 / 通视分析（考虑高度的真三维）。
 *
 * 从观察点按 方位角 × 俯仰角 发射射线，用 scene.pickFromRay 检测是否被建筑/地形遮挡，
 * 生成射线可视化 + 视域覆盖区域面。
 *   - 不同俯仰角 → 射线有向上/向下分量，能体现高楼对视线的三维遮挡；
 *   - 绿色=可视、红色=被挡，并按可视线命中点标出可视边界。
 *
 * 同时提供天际线分析：从观察点向外发射水平射线，绘制建筑/地形轮廓线。
 * 观察点离地高度（eyeHeight）可调，模拟不同楼层/瞭望高度。
 */
import * as Cesium from 'cesium'

async function sampleGround(viewer, lon, lat) {
  const carto = Cesium.Cartographic.fromDegrees(lon, lat)
  try {
    const [u] = await Cesium.sampleTerrainMostDetailed(viewer.terrainProvider, [carto])
    return u.height || 0
  } catch { return 0 }
}

// ══════════════════════════════════════════════════════════════
//  视域分析（增强版）
// ══════════════════════════════════════════════════════════════

/**
 * 执行视域分析，可视化射线 + 视域覆盖区域面。
 *
 * @param {Cesium.Viewer} viewer
 * @param {LayerManager} layerManager
 * @param {[number,number]} observerLonLat 观察点 [lon, lat]
 * @param {number} eyeHeight  观察点离地高度（米），默认 30m
 * @param {number} radius     分析半径（米），默认 600m
 * @param {Object} opts      可选配置
 * @param {number} opts.azimuths  方位采样数（默认 96）
 * @param {number[]} opts.pitches 俯仰角数组（度），默认 [-8, 0, 8, 18]
 * @param {boolean} opts.showArea 是否生成视域覆盖区域面（默认 true）
 * @returns 分析统计结果
 */
export async function viewshed(viewer, layerManager, observerLonLat, eyeHeight = 30, radius = 600, opts = {}) {
  const { azimuths: AZ = 96, pitches: PITCHES = [-8, 0, 8, 18], showArea = true } = opts
  const [olon, olat] = observerLonLat
  const ground = await sampleGround(viewer, olon, olat)
  const origin = Cesium.Cartesian3.fromDegrees(olon, olat, ground + eyeHeight)

  layerManager.clear('viewshed')
  const ds = layerManager._ds('viewshed')

  // ── 观察点标记（立柱 + 顶点 + 标签） ──
  ds.entities.add({
    position: Cesium.Cartesian3.fromDegrees(olon, olat, ground + eyeHeight / 2),
    cylinder: { length: eyeHeight, topRadius: 2, bottomRadius: 2,
      material: Cesium.Color.YELLOW.withAlpha(0.5) }
  })
  ds.entities.add({
    position: origin,
    point: { pixelSize: 12, color: Cesium.Color.YELLOW, outlineColor: Cesium.Color.BLACK,
      outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
    label: { text: `👁 观察点 +${eyeHeight}m`, font: '13px sans-serif',
      pixelOffset: new Cesium.Cartesian2(0, -22), showBackground: true,
      backgroundColor: Cesium.Color.fromBytes(10, 16, 30, 220),
      disableDepthTestDistance: Number.POSITIVE_INFINITY }
  })

  // 本地 ENU 坐标系，用于构造方向
  const enu = Cesium.Transforms.eastNorthUpToFixedFrame(origin)
  let visible = 0, total = 0

  // 收集可视射线末端点（用于绘制视域覆盖区域面）
  const visEndpoints = []
  const blockedEndpoints = []

  for (let a = 0; a < AZ; a++) {
    const az = (a / AZ) * 2 * Math.PI
    for (const pdeg of PITCHES) {
      total++
      const p = Cesium.Math.toRadians(pdeg)
      // ENU 下的方向向量：x=东, y=北, z=上
      const local = new Cesium.Cartesian3(
        Math.cos(p) * Math.sin(az), Math.cos(p) * Math.cos(az), Math.sin(p))
      const dirWorld = Cesium.Matrix4.multiplyByPointAsVector(enu, local, new Cesium.Cartesian3())
      const dir = Cesium.Cartesian3.normalize(dirWorld, new Cesium.Cartesian3())
      const target = Cesium.Cartesian3.add(
        origin, Cesium.Cartesian3.multiplyByScalar(dir, radius, new Cesium.Cartesian3()),
        new Cesium.Cartesian3())

      const hit = viewer.scene.pickFromRay(new Cesium.Ray(origin, dir), [])
      let end = target, isVis = true, hitHeight = 0
      if (hit && Cesium.defined(hit.position)) {
        const dHit = Cesium.Cartesian3.distance(origin, hit.position)
        if (dHit < radius - 5) {
          end = hit.position
          isVis = false
          const hCarto = Cesium.Cartographic.fromCartesian(hit.position)
          hitHeight = hCarto.height || 0
        }
      }
      if (isVis) {
        visible++
        const endCarto = Cesium.Cartographic.fromCartesian(end)
        visEndpoints.push({
          lon: Cesium.Math.toDegrees(endCarto.longitude),
          lat: Cesium.Math.toDegrees(endCarto.latitude),
          height: endCarto.height || 0
        })
      } else {
        const endCarto = Cesium.Cartographic.fromCartesian(end)
        blockedEndpoints.push({
          lon: Cesium.Math.toDegrees(endCarto.longitude),
          lat: Cesium.Math.toDegrees(endCarto.latitude),
          height: hitHeight
        })
      }

      // 绘制射线
      ds.entities.add({
        polyline: { positions: [origin, end], width: pdeg === 0 ? 2.2 : 1.1,
          material: isVis ? Cesium.Color.fromBytes(120, 246, 200, 150)
            : Cesium.Color.fromBytes(255, 92, 124, 140) }
      })
    }
  }

  // ── 视域覆盖区域面（多边形） ──
  if (showArea && visEndpoints.length >= 3) {
    renderViewshedArea(ds, origin, visEndpoints)
  }

  // ── 被遮挡边界线 ──
  if (blockedEndpoints.length >= 3) {
    renderBlockedBoundary(ds, blockedEndpoints)
  }

  const ratio = total > 0 ? +(visible / total).toFixed(3) : 0
  return { visible, blocked: total - visible, total, ratio, eyeHeight, radius,
    observerPos: observerLonLat }
}

/**
 * 渲染视域覆盖区域面（绿色半透明多边形）。
 */
function renderViewshedArea(ds, origin, endpoints) {
  // 按方位角排序端点，构建多边形
  const originCarto = Cesium.Cartographic.fromCartesian(origin)
  const oLon = Cesium.Math.toDegrees(originCarto.longitude)
  const oLat = Cesium.Math.toDegrees(originCarto.latitude)
  const sorted = endpoints.sort((a, b) => {
    const aAz = Math.atan2(a.lon - oLon, a.lat - oLat)
    const bAz = Math.atan2(b.lon - oLon, b.lat - oLat)
    return aAz - bAz
  })
  // 构建多边形环：sorted + 回到第一个形成闭合
  const ring = sorted.map(p => [p.lon, p.lat])
  ring.push(ring[0])
  // 展平为 Cesium 接受的数组格式
  const flatRing = ring.flat()
  ds.entities.add({
    polygon: {
      hierarchy: Cesium.Cartesian3.fromDegreesArray(flatRing),
      material: Cesium.Color.fromBytes(120, 246, 200, 60),
      outline: true,
      outlineColor: Cesium.Color.fromBytes(120, 246, 200, 180),
      outlineWidth: 1.5,
      height: 0,
      classificationType: Cesium.ClassificationType.TERRAIN
    }
  })
}

/**
 * 渲染被遮挡区域边界线（红色虚线）。
 */
function renderBlockedBoundary(ds, endpoints) {
  if (endpoints.length < 2) return
  const positions = endpoints.map(p =>
    Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height + 3))
  ds.entities.add({
    polyline: {
      positions,
      width: 2,
      material: new Cesium.PolylineDashMaterialProperty({
        color: Cesium.Color.fromBytes(255, 92, 124, 160),
        dashLength: 10
      }),
      clampToGround: false
    }
  })
}

// ══════════════════════════════════════════════════════════════
//  天际线分析（从观察点向外看）
// ══════════════════════════════════════════════════════════════

/**
 * 从观察点计算天际线轮廓。
 * 水平方向采样，在每个方位发射测高射线，找最远可见点。
 *
 * @param {Cesium.Viewer} viewer
 * @param {[number,number]} observerLonLat 观察点
 * @param {number} eyeHeight     离地高度（米）
 * @param {number} samples       采样数（默认 360）
 * @param {number} maxDist       最大探测距离（米），默认 8000
 * @returns 天际线数据数组
 */
export async function skylineFromObserver(viewer, observerLonLat, eyeHeight = 30, samples = 360, maxDist = 8000) {
  const [olon, olat] = observerLonLat
  const ground = await sampleGround(viewer, olon, olat)
  const origin = Cesium.Cartesian3.fromDegrees(olon, olat, ground + eyeHeight)

  const enuMatrix = Cesium.Transforms.eastNorthUpToFixedFrame(origin)
  const skyline = []

  for (let i = 0; i < samples; i++) {
    const azimuth = (i / samples) * 2 * Math.PI
    // 水平方向（忽略俯仰角，专注天际线）
    const localDir = new Cesium.Cartesian3(Math.sin(azimuth), Math.cos(azimuth), 0)
    const dirWorld = Cesium.Matrix4.multiplyByPointAsVector(enuMatrix, localDir, new Cesium.Cartesian3())
    Cesium.Cartesian3.normalize(dirWorld, dirWorld)

    const ray = new Cesium.Ray(origin, dirWorld)
    const hit = viewer.scene.pickFromRay(ray, [])

    let endPoint, endHeight
    if (hit && Cesium.defined(hit.position)) {
      const dist = Cesium.Cartesian3.distance(origin, hit.position)
      if (dist < maxDist) {
        endPoint = hit.position
        const carto = Cesium.Cartographic.fromCartesian(endPoint)
        endHeight = carto.height || 0
      }
    }
    if (!endPoint) {
      endPoint = Cesium.Cartesian3.add(origin,
        Cesium.Cartesian3.multiplyByScalar(dirWorld, maxDist, new Cesium.Cartesian3()),
        new Cesium.Cartesian3())
      endHeight = 0
    }

    const endCarto = Cesium.Cartographic.fromCartesian(endPoint)
    const dist = Cesium.Cartesian3.distance(origin, endPoint)
    const elevation = dist > 0
      ? Math.atan2(endHeight - (ground + eyeHeight), dist) * Cesium.Math.DEGREES_PER_RADIAN
      : 0

    skyline.push({
      azimuth: +(azimuth * Cesium.Math.DEGREES_PER_RADIAN).toFixed(1),
      elevation: +elevation.toFixed(2),
      lon: +Cesium.Math.toDegrees(endCarto.longitude).toFixed(6),
      lat: +Cesium.Math.toDegrees(endCarto.latitude).toFixed(6),
      height: +endHeight.toFixed(2),
      distance: +dist.toFixed(1)
    })
  }
  return skyline
}

/**
 * 在场景中渲染天际线。
 * @param {LayerManager} layerManager
 * @param {Array} skylineData  天际线数据
 * @param {string} color       颜色（默认金色）
 */
export function renderSkylineLayer(layerManager, skylineData, color = '#ffd24d') {
  if (!skylineData || !skylineData.length) return
  layerManager.clear('skyline')
  const ds = layerManager._ds('skyline')

  const c = Cesium.Color.fromCssColorString(color)
  const positions = skylineData.map(p =>
    Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height + 3))

  // 主轮廓线
  ds.entities.add({
    polyline: {
      positions,
      width: 2.2,
      material: c.withAlpha(0.85),
      clampToGround: false
    }
  })

  // 最高点标注
  let maxP = skylineData[0]
  for (const p of skylineData) {
    if (p.elevation > maxP.elevation) maxP = p
  }
  ds.entities.add({
    position: Cesium.Cartesian3.fromDegrees(maxP.lon, maxP.lat, maxP.height + 20),
    point: { pixelSize: 8, color: c, disableDepthTestDistance: Number.POSITIVE_INFINITY },
    label: {
      text: `${maxP.elevation.toFixed(1)}°`,
      font: '11px sans-serif',
      fillColor: c,
      showBackground: true,
      backgroundColor: Cesium.Color.fromBytes(10, 16, 30, 200),
      pixelOffset: new Cesium.Cartesian2(0, -14),
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    }
  })

  // 统计信息
  const elevations = skylineData.map(p => p.elevation)
  const maxEl = Math.max(...elevations)
  const minEl = Math.min(...elevations)
  const avgEl = elevations.reduce((a, b) => a + b, 0) / elevations.length
  return { maxElevation: maxEl, minElevation: minEl, avgElevation: +avgEl.toFixed(2), samples: skylineData.length }
}

// ══════════════════════════════════════════════════════════════
//  交互式视域（跟随观察点移动实时更新）
// ══════════════════════════════════════════════════════════════

/**
 * 创建交互式视域：观察点跟随鼠标/触控拖动实时更新。
 * 调用返回的 start() 开始交互，stop() 结束。
 *
 * @returns {{ start: Function, stop: Function, isActive: Function }}
 */
export function createInteractiveViewshed(viewer, layerManager, eyeHeight = 30, radius = 600) {
  let active = false
  let handler = null
  let _updateTimer = null

  async function updateAt(position) {
    const cartesian = viewer.scene.pickPosition(position) ||
      viewer.camera.pickEllipsoid(position, viewer.scene.globe.ellipsoid)
    if (!cartesian) return
    const carto = Cesium.Cartographic.fromCartesian(cartesian)
    const lonlat = [
      +Cesium.Math.toDegrees(carto.longitude).toFixed(6),
      +Cesium.Math.toDegrees(carto.latitude).toFixed(6)
    ]
    // 节流更新（每 150ms 最多一次）
    if (_updateTimer) return
    _updateTimer = setTimeout(async () => {
      _updateTimer = null
      await viewshed(viewer, layerManager, lonlat, eyeHeight, radius)
    }, 150)
  }

  return {
    start() {
      if (active) return
      active = true
      handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
      handler.setInputAction((move) => {
        updateAt(move.endPosition)
      }, Cesium.ScreenSpaceEventType.MOUSE_MOVE)
    },
    stop() {
      active = false
      if (handler) { handler.destroy(); handler = null }
      if (_updateTimer) { clearTimeout(_updateTimer); _updateTimer = null }
    },
    isActive() { return active }
  }
}

// ══════════════════════════════════════════════════════════════
//  建筑限高分析（辅助工具）
// ══════════════════════════════════════════════════════════════

/**
 * 检测观察点视线是否被特定方向建筑遮挡。
 * 在给定的方位角上，沿水平方向扫描，返回第一个遮挡建筑的信息。
 *
 * @returns {{ blocked: boolean, distance: number|null, height: number|null }}
 */
export function checkLineOfSight(viewer, observerLonLat, eyeHeight, azimuthDeg, maxDist = 5000) {
  const [olon, olat] = observerLonLat
  // 简化：取地面高程0，实际应由调用方传入
  const origin = Cesium.Cartesian3.fromDegrees(olon, olat, eyeHeight)
  const az = Cesium.Math.toRadians(azimuthDeg)
  const enu = Cesium.Transforms.eastNorthUpToFixedFrame(origin)
  const local = new Cesium.Cartesian3(Math.sin(az), Math.cos(az), 0)
  const dirWorld = Cesium.Matrix4.multiplyByPointAsVector(enu, local, new Cesium.Cartesian3())
  Cesium.Cartesian3.normalize(dirWorld, dirWorld)

  const ray = new Cesium.Ray(origin, dirWorld)
  const hit = viewer.scene.pickFromRay(ray, [])
  if (hit && Cesium.defined(hit.position)) {
    const dist = Cesium.Cartesian3.distance(origin, hit.position)
    if (dist < maxDist) {
      const carto = Cesium.Cartographic.fromCartesian(hit.position)
      return { blocked: true, distance: +dist.toFixed(1), height: +(carto.height || 0).toFixed(1) }
    }
  }
  return { blocked: false, distance: null, height: null }
}
