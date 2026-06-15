/**
 * Cesium 三维场景管理。
 * 初始化、白模、底图切换（影像/带注记街道，均 WGS84 杜绝偏移）、相机、点选、日照。
 */
import * as Cesium from 'cesium'

Cesium.Ion.defaultAccessToken =
  import.meta.env.VITE_CESIUM_ION_TOKEN || Cesium.Ion.defaultAccessToken

let currentImagery = null   // 记录当前底图图层，便于夜间调暗

export async function createViewer(container) {
  const viewer = new Cesium.Viewer(container, {
    terrain: Cesium.Terrain.fromWorldTerrain(),
    baseLayer: false,            // 自行管理底图
    animation: false, timeline: false, baseLayerPicker: false, geocoder: false,
    homeButton: false, sceneModePicker: false, navigationHelpButton: false,
    fullscreenButton: false, infoBox: false, selectionIndicator: false
  })
  viewer.scene.globe.enableLighting = true
  viewer.scene.skyAtmosphere.show = true
  viewer.scene.fog.enabled = true
  viewer.scene.globe.depthTestAgainstTerrain = false  // 避免建筑被地形裁切产生条状伪影
  // 关键：地形只"接收"阴影、不"投射"——否则山体会在城市上拉出超长黑条
  viewer.terrainShadows = Cesium.ShadowMode.RECEIVE_ONLY
  viewer.shadows = true                               // 默认开启日照阴影（建筑投影）
  _tuneShadowMap(viewer)
  viewer.cesiumWidget.creditContainer.style.display = 'none'
  await setBasemap(viewer, 'satellite_labels')        // 默认带注记影像
  return viewer
}

/** 阴影贴图调优：柔和、限制范围以提升近处阴影质量、减轻伪影。 */
function _tuneShadowMap(viewer) {
  const sm = viewer.shadowMap
  sm.softShadows = true
  sm.size = 2048
  sm.darkness = 0.45
  sm.maximumDistance = 5000      // 集中阴影分辨率到近处街区，远处不投影避免长条
}

/** 开/关日照阴影。 */
export function setShadows(viewer, on) {
  viewer.shadows = on
  if (on) _tuneShadowMap(viewer)
}

let _worldTerrain = null   // 缓存世界地形 provider，避免反复创建

async function _terrainHeight(lon, lat) {
  try {
    if (!_worldTerrain) _worldTerrain = await Cesium.createWorldTerrainAsync()
    const [s] = await Cesium.sampleTerrainMostDetailed(
      _worldTerrain, [Cesium.Cartographic.fromDegrees(lon, lat)])
    return s.height || 0
  } catch { return 0 }
}

/** 给白模整体施加竖直偏移（dz 米）。dz=0 复位贴回地形。 */
function _offsetTileset(tileset, dz) {
  if (!tileset) return
  if (!dz) { tileset.modelMatrix = Cesium.Matrix4.IDENTITY; return }
  const carto = Cesium.Cartographic.fromCartesian(tileset.boundingSphere.center)
  const from = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, 0)
  const to = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, dz)
  const t = Cesium.Cartesian3.subtract(to, from, new Cesium.Cartesian3())
  tileset.modelMatrix = Cesium.Matrix4.fromTranslation(t)
}

/**
 * 开/关三维地形起伏。关闭时用椭球面（纯平），适合街道底图——否则街道瓦片贴在
 * 起伏地形上会被拉伸、看着很奇怪。
 *
 * 关地形时地面落到椭球面(高程0)，而 OSM 白模基座仍在真实高程 → 会“悬空”。
 * 因此关地形前先取中心点地形高程，再把白模整体下移该高程，让它重新落到平面地面上。
 */
export async function setTerrain(viewer, on, tileset = null, center = null) {
  if (on) {
    if (!_worldTerrain) _worldTerrain = await Cesium.createWorldTerrainAsync()
    viewer.terrainProvider = _worldTerrain
    _offsetTileset(tileset, 0)                 // 复位：白模贴回地形
  } else {
    const h = center ? await _terrainHeight(center[0], center[1]) : 0
    viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider()
    _offsetTileset(tileset, -h)                // 下移地形高度，避免悬空
  }
}

/**
 * 切换底图。所有可选底图均为 WGS84 坐标系，与 OSM 白模、分析图层天然对齐，
 * 不会出现"卫星图与街道图偏移"的问题（不混用 GCJ-02 的高德/百度瓦片）。
 *   type: 'satellite' | 'satellite_labels' | 'street'
 */
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
  // 实心白模（不透明），避免大量半透明建筑在斜视下糊成一片蓝
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
  const [lon, lat] = cfg.center
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat - cfg.camera_height / 220000, cfg.camera_height),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-38), roll: 0 },
    duration: 2.2
  })
}

/** 注册地图点选，回调返回 [lon,lat]。 */
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

/**
 * 设定一天中的时刻（小时，支持小数 → 丝滑），驱动太阳光照与建筑阴影。
 * darkNight=true 时夜间进一步压暗（降低底图亮度 + 大气）。
 */
export function setSunlight(viewer, hour, darkNight = true) {
  // 注意：不在此处开启 viewer.shadows，硬阴影由 setShadows 单独控制
  viewer.scene.globe.enableLighting = true
  const base = Cesium.JulianDate.fromIso8601('2024-06-21T00:00:00+08:00')
  viewer.clock.currentTime = Cesium.JulianDate.addSeconds(base, hour * 3600, new Cesium.JulianDate())

  // 昼夜亮度：白天(7-17)全亮，黎明/黄昏渐变，深夜最暗
  let b
  if (hour < 5 || hour > 19) b = 0.18                 // 深夜
  else if (hour < 7) b = 0.18 + (hour - 5) / 2 * 0.82  // 黎明 5→7
  else if (hour > 17) b = 1.0 - (hour - 17) / 2 * 0.82 // 黄昏 17→19
  else b = 1.0                                         // 白天
  if (currentImagery) currentImagery.brightness = darkNight ? b : 1.0
  viewer.scene.skyAtmosphere.brightnessShift = darkNight ? (b - 1) * 0.6 : 0
}
