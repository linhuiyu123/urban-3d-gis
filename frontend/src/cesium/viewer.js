/**
 * Cesium 三维场景管理。
 * 初始化、白模、底图切换（影像/带注记/街道，均 WGS84 杜绝偏移）、相机、点选、日照、
 * 天际线剪影、建筑高亮、测距工具、指北针、相机预设。
 */
import * as Cesium from 'cesium'

Cesium.Ion.defaultAccessToken =
  import.meta.env.VITE_CESIUM_ION_TOKEN || Cesium.Ion.defaultAccessToken

let currentImagery = null   // 记录当前底图图层，便于夜间调暗

// 高亮/测距/指北针的内部状态
let _highlighted = null
let _originalStyle = null

export async function createViewer(container) {
  const viewer = new Cesium.Viewer(container, {
    terrain: Cesium.Terrain.fromWorldTerrain(),
    baseLayer: false,
    animation: false, timeline: false, baseLayerPicker: false, geocoder: false,
    homeButton: false, sceneModePicker: false, navigationHelpButton: false,
    fullscreenButton: false, infoBox: false, selectionIndicator: false
  })
  viewer.scene.globe.enableLighting = true
  viewer.scene.skyAtmosphere.show = true
  viewer.scene.fog.enabled = true
  viewer.scene.globe.depthTestAgainstTerrain = false
  viewer.terrainShadows = Cesium.ShadowMode.RECEIVE_ONLY
  viewer.shadows = true
  _tuneShadowMap(viewer)
  viewer.cesiumWidget.creditContainer.style.display = 'none'
  await setBasemap(viewer, 'satellite_labels')
  return viewer
}

function _tuneShadowMap(viewer) {
  const sm = viewer.shadowMap
  sm.softShadows = true
  sm.size = 2048
  sm.darkness = 0.45
  sm.maximumDistance = 5000
}

export function setShadows(viewer, on) {
  viewer.shadows = on
  if (on) _tuneShadowMap(viewer)
}

let _worldTerrain = null

async function _terrainHeight(lon, lat) {
  try {
    if (!_worldTerrain) _worldTerrain = await Cesium.createWorldTerrainAsync()
    const [s] = await Cesium.sampleTerrainMostDetailed(
      _worldTerrain, [Cesium.Cartographic.fromDegrees(lon, lat)])
    return s.height || 0
  } catch { return 0 }
}

function _offsetTileset(tileset, dz) {
  if (!tileset) return
  if (!dz) { tileset.modelMatrix = Cesium.Matrix4.IDENTITY; return }
  const carto = Cesium.Cartographic.fromCartesian(tileset.boundingSphere.center)
  const from = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, 0)
  const to = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, dz)
  const t = Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3())
  tileset.modelMatrix = Cesium.Matrix4.fromTranslation(t)
}

export async function setTerrain(viewer, on, tileset = null, center = null) {
  if (on) {
    if (!_worldTerrain) _worldTerrain = await Cesium.createWorldTerrainAsync()
    viewer.terrainProvider = _worldTerrain
    _offsetTileset(tileset, 0)
  } else {
    const h = center ? await _terrainHeight(center[0], center[1]) : 0
    viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider()
    _offsetTileset(tileset, -h)
  }
}

export async function setBasemap(viewer, type) {
  const layers = viewer.imageryLayers
  layers.removeAll()
  let provider
  if (type === 'street') {
    provider = new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/' })
  } else {
    provider = await Cesium.createWorldImageryAsync({
      style: type === 'satellite_labels'
        ? Cesium.IonWorldImageryStyle.AERIAL_WITH_LABELS
        : Cesium.IonWorldImageryStyle.AERIAL
    })
  }
  currentImagery = layers.addImageryProvider(provider)
  return currentImagery
}

export async function loadOsmBuildings(viewer) {
  const tileset = await Cesium.createOsmBuildingsAsync()
  viewer.scene.primitives.add(tileset)
  applyWhiteStyle(tileset)
  return tileset
}

export function applyWhiteStyle(tileset) {
  tileset.style = new Cesium.Cesium3DTileStyle({ color: "color('#dfe6f2')" })
}

export function styleBuildingsByHeight(tileset, on) {
  if (!on) { applyWhiteStyle(tileset); return }
  tileset.style = new Cesium.Cesium3DTileStyle({
    color: {
      conditions: [
        ["${feature['cesium#estimatedHeight']} >= 120", "color('#ff5c7c')"],
        ["${feature['cesium#estimatedHeight']} >= 80", "color('#ff9d5c')"],
        ["${feature['cesium#estimatedHeight']} >= 45", "color('#ffe07c')"],
        ["${feature['cesium#estimatedHeight']} >= 20", "color('#7cf6c8')"],
        ["true", "color('#4da3ff')"]
      ]
    }
  })
}

export function flyToCity(viewer, cfg) {
  if (cfg.bbox && cfg.bbox.length === 4) {
    const [west, south, east, north] = cfg.bbox
    const rect = Cesium.Rectangle.fromDegrees(west, south, east, north)
    const sphere = Cesium.BoundingSphere.fromRectangle3D(rect, Cesium.Ellipsoid.WGS84, 0)
    const range = Math.max(cfg.camera_height || 0, sphere.radius * 2.1)
    viewer.camera.flyToBoundingSphere(sphere, {
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-55), range),
      duration: 2.2
    })
    return
  }

  const [lon, lat] = cfg.center
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat, cfg.camera_height),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-38), roll: 0 },
    duration: 2.2
  })
}

export function onMapClick(viewer, callback) {
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
  handler.setInputAction((click) => {
    const cartesian = viewer.scene.pickPosition(click.position) ||
      viewer.camera.pickEllipsoid(click.position, viewer.scene.globe.ellipsoid)
    if (!cartesian) return
    const c = Cesium.Cartographic.fromCartesian(cartesian)
    callback([
      +Cesium.Math.toDegrees(c.longitude).toFixed(6),
      +Cesium.Math.toDegrees(c.latitude).toFixed(6)
    ])
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
  return handler
}

export function setSunlight(viewer, hour, darkNight = true) {
  viewer.scene.globe.enableLighting = true
  const base = Cesium.JulianDate.fromIso8601('2024-06-21T00:00:00+08:00')
  viewer.clock.currentTime = Cesium.JulianDate.addSeconds(base, hour * 3600, new Cesium.JulianDate())
  let b
  if (hour < 5 || hour > 19) b = 0.18
  else if (hour < 7) b = 0.18 + (hour - 5) / 2 * 0.82
  else if (hour > 17) b = 1.0 - (hour - 17) / 2 * 0.82
  else b = 1.0
  if (currentImagery) currentImagery.brightness = darkNight ? b : 1.0
  viewer.scene.skyAtmosphere.brightnessShift = darkNight ? (b - 1) * 0.6 : 0
}

// ══════════════════════════════════════════════════════════════
//  建筑高亮（点选建筑）
// ══════════════════════════════════════════════════════════════

/**
 * 启用建筑点选高亮。点击建筑时高亮为金色；按 Escape 恢复。
 * 返回 handler 以便销毁。
 */
export function enableBuildingHighlight(viewer, tileset, layerManager) {
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)

  handler.setInputAction((click) => {
    const picked = viewer.scene.pick(click.position)
    if (picked && picked.primitive === tileset) {
      highlightBuilding(tileset)
    } else {
      clearHighlight(tileset)
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK)

  // Escape 键恢复
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') clearHighlight(tileset)
  })

  return handler
}

export function highlightBuilding(tileset) {
  clearHighlight(tileset)
  _originalStyle = tileset.style
  tileset.style = new Cesium.Cesium3DTileStyle({
    color: {
      conditions: [
        ["true", "color('#ffd24d')"]
      ]
    }
  })
  _highlighted = tileset
}

export function clearHighlight(tileset) {
  if (_originalStyle && tileset) {
    tileset.style = _originalStyle
    _originalStyle = null
  }
  _highlighted = null
}

export function resetBuildingStyle(tileset, heightOn) {
  _originalStyle = null
  _highlighted = null
  if (heightOn) styleBuildingsByHeight(tileset, true)
  else applyWhiteStyle(tileset)
}

// ══════════════════════════════════════════════════════════════
//  天际线剪影
// ══════════════════════════════════════════════════════════════

/**
 * 从当前相机位置计算天际线剪影。
 * 沿水平方向采样，每个方位找最远的可见遮挡点。
 * @param {number} samples 采样数（默认 180）
 * @returns {Array<{azimuth:number, elevation:number, lon:number, lat:number, height:number}>}
 */
export function computeSkyline(viewer, samples = 180) {
  const cameraPos = viewer.camera.positionWC
  const cameraCarto = Cesium.Cartographic.fromCartesian(cameraPos)
  const camHeight = cameraCarto.height
  const maxDist = 15000

  const skyline = []
  const enuMatrix = Cesium.Transforms.eastNorthUpToFixedFrame(cameraPos)

  for (let i = 0; i < samples; i++) {
    const azimuth = (i / samples) * 2 * Math.PI
    const localDir = new Cesium.Cartesian3(Math.sin(azimuth), Math.cos(azimuth), 0.05)
    const dirWorld = Cesium.Matrix4.multiplyByPointAsVector(enuMatrix, localDir, new Cesium.Cartesian3())
    Cesium.Cartesian3.normalize(dirWorld, dirWorld)

    const ray = new Cesium.Ray(cameraPos, dirWorld)
    const hit = viewer.scene.pickFromRay(ray, [])

    let endPoint, endHeight
    if (hit && Cesium.defined(hit.position)) {
      const dist = Cesium.Cartesian3.distance(cameraPos, hit.position)
      if (dist < maxDist) {
        endPoint = hit.position
        const carto = Cesium.Cartographic.fromCartesian(endPoint)
        endHeight = carto.height || 0
      }
    }

    if (!endPoint) {
      endPoint = Cesium.Cartesian3.add(
        cameraPos,
        Cesium.Cartesian3.multiplyByScalar(dirWorld, maxDist, new Cesium.Cartesian3()),
        new Cesium.Cartesian3()
      )
      endHeight = 0
    }

    const endCarto = Cesium.Cartographic.fromCartesian(endPoint)
    const dist = Cesium.Cartesian3.distance(cameraPos, endPoint)
    const elevation = dist > 0
      ? Math.atan2(endHeight - camHeight, dist) * Cesium.Math.DEGREES_PER_RADIAN
      : 0

    skyline.push({
      azimuth: +(azimuth * Cesium.Math.DEGREES_PER_RADIAN).toFixed(1),
      elevation: +elevation.toFixed(2),
      lon: +Cesium.Math.toDegrees(endCarto.longitude).toFixed(6),
      lat: +Cesium.Math.toDegrees(endCarto.latitude).toFixed(6),
      height: +endHeight.toFixed(2)
    })
  }
  return skyline
}

/**
 * 在场景中渲染天际线（金色虚线折线 + 最高点标注）。
 */
export function renderSkyline(layerManager, skylineData, name = 'skyline') {
  if (!skylineData || !skylineData.length) return
  layerManager.clear(name)
  const ds = layerManager._ds(name)
  const positions = skylineData.map(p =>
    Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height + 5)
  )
  ds.entities.add({
    polyline: {
      positions,
      width: 2.5,
      material: new Cesium.PolylineDashMaterialProperty({
        color: Cesium.Color.GOLD.withAlpha(0.85),
        dashLength: 14
      }),
      clampToGround: false
    }
  })
  let maxP = skylineData[0]
  for (const p of skylineData) {
    if (p.elevation > maxP.elevation) maxP = p
  }
  ds.entities.add({
    position: Cesium.Cartesian3.fromDegrees(maxP.lon, maxP.lat, maxP.height + 25),
    point: { pixelSize: 8, color: Cesium.Color.GOLD, disableDepthTestDistance: Number.POSITIVE_INFINITY },
    label: {
      text: `最高 +${maxP.elevation.toFixed(1)}°`,
      font: '12px sans-serif',
      fillColor: Cesium.Color.GOLD,
      showBackground: true,
      backgroundColor: Cesium.Color.fromBytes(10, 16, 30, 210),
      pixelOffset: new Cesium.Cartesian2(0, -16),
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    }
  })
}

// ══════════════════════════════════════════════════════════════
//  测距工具
// ══════════════════════════════════════════════════════════════

/**
 * 创建测距工具实例。
 * 调用 .enable() 开始测距（左键加点、双击结束），.disable() 关闭。
 */
export function createMeasureTool(viewer, layerManager) {
  let points = []
  let tempEntities = []
  let handler = null
  let active = false

  function cleanup() {
    tempEntities.forEach(e => {
      try { layerManager._ds('measure').entities.remove(e) } catch (_) { }
    })
    tempEntities = []
  }

  function clearAll() {
    cleanup()
    points = []
    layerManager.clear('measure')
  }

  function addPoint(position) {
    const cartesian = viewer.scene.pickPosition(position) ||
      viewer.camera.pickEllipsoid(position, viewer.scene.globe.ellipsoid)
    if (!cartesian) return null
    const carto = Cesium.Cartographic.fromCartesian(cartesian)
    const lon = +Cesium.Math.toDegrees(carto.longitude).toFixed(6)
    const lat = +Cesium.Math.toDegrees(carto.latitude).toFixed(6)
    points.push({ lon, lat, cartesian, height: carto.height || 0 })
    return { lon, lat }
  }

  function renderMeasure() {
    cleanup()
    const ds = layerManager._ds('measure')
    for (let i = 0; i < points.length; i++) {
      const p = points[i]
      const e = ds.entities.add({
        position: p.cartesian,
        point: { pixelSize: 7, color: Cesium.Color.CYAN, outlineColor: Cesium.Color.BLACK, outlineWidth: 1,
          disableDepthTestDistance: Number.POSITIVE_INFINITY },
        label: {
          text: `P${i + 1}`,
          font: 'bold 12px sans-serif',
          fillColor: Cesium.Color.WHITE,
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString('#0078d4'),
          pixelOffset: new Cesium.Cartesian2(10, -8),
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }
      })
      tempEntities.push(e)
    }
    let totalDist = 0
    for (let i = 1; i < points.length; i++) {
      const seg = Cesium.Cartesian3.distance(points[i - 1].cartesian, points[i].cartesian)
      totalDist += seg
      const mid = Cesium.Cartesian3.lerp(points[i - 1].cartesian, points[i].cartesian, 0.5, new Cesium.Cartesian3())
      tempEntities.push(ds.entities.add({
        polyline: {
          positions: [points[i - 1].cartesian, points[i].cartesian],
          width: 2.5,
          material: Cesium.Color.CYAN.withAlpha(0.75),
          clampToGround: false
        }
      }))
      tempEntities.push(ds.entities.add({
        position: mid,
        label: {
          text: seg > 1000 ? `${(seg / 1000).toFixed(2)} km` : `${seg.toFixed(1)} m`,
          font: '11px sans-serif',
          fillColor: Cesium.Color.CYAN,
          showBackground: true,
          backgroundColor: Cesium.Color.fromBytes(10, 16, 30, 190),
          pixelOffset: new Cesium.Cartesian2(0, -12),
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }
      }))
    }
    return totalDist
  }

  return {
    enable() {
      if (active) return
      active = true
      clearAll()
      handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
      handler.setInputAction((click) => {
        const result = addPoint(click.position)
        if (result) renderMeasure()
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
      handler.setInputAction(() => {
        active = false
        if (handler) { handler.destroy(); handler = null }
        const total = renderMeasure()
        if (total > 0 && points.length >= 2) {
          const ds = layerManager._ds('measure')
          ds.entities.add({
            position: Cesium.Cartesian3.add(points[points.length - 1].cartesian, new Cesium.Cartesian3(0, 0, 35), new Cesium.Cartesian3()),
            label: {
              text: `∑ ${total > 1000 ? (total / 1000).toFixed(2) + ' km' : total.toFixed(1) + ' m'}`,
              font: 'bold 13px sans-serif',
              fillColor: Cesium.Color.GOLD,
              showBackground: true,
              backgroundColor: Cesium.Color.fromBytes(10, 16, 30, 220),
              pixelOffset: new Cesium.Cartesian2(0, -20),
              disableDepthTestDistance: Number.POSITIVE_INFINITY
            }
          })
        }
      }, Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK)
    },
    disable() {
      active = false
      if (handler) { handler.destroy(); handler = null }
      clearAll()
    },
    getResult() {
      if (points.length < 2) return { distance: 0, points: [...points] }
      let dist = 0
      for (let i = 1; i < points.length; i++) {
        dist += Cesium.Cartesian3.distance(points[i - 1].cartesian, points[i].cartesian)
      }
      return { distance: +dist.toFixed(2), points: points.map(p => ({ lon: p.lon, lat: p.lat })) }
    },
    isActive() { return active },
    clear() { clearAll() }
  }
}

// ══════════════════════════════════════════════════════════════
//  相机预设
// ══════════════════════════════════════════════════════════════

/** 预设视点切换。 */
export function cameraPreset(viewer, preset) {
  const presets = {
    topDown: { pitch: Cesium.Math.toRadians(-90), heading: 0 },
    birdEye: { pitch: Cesium.Math.toRadians(-45), heading: 0 },
    streetLevel: { pitch: Cesium.Math.toRadians(-18), heading: 0 }
  }
  const p = presets[preset]
  if (!p) return
  viewer.camera.flyTo({
    destination: viewer.camera.positionWC,
    orientation: { heading: p.heading, pitch: p.pitch, roll: 0 },
    duration: 0.8
  })
}

/** 获取当前相机状态。 */
export function getCameraState(viewer) {
  const carto = Cesium.Cartographic.fromCartesian(viewer.camera.positionWC)
  return {
    lon: +Cesium.Math.toDegrees(carto.longitude).toFixed(6),
    lat: +Cesium.Math.toDegrees(carto.latitude).toFixed(6),
    height: +carto.height.toFixed(1),
    heading: +Cesium.Math.toDegrees(viewer.camera.heading).toFixed(1),
    pitch: +Cesium.Math.toDegrees(viewer.camera.pitch).toFixed(1)
  }
}
