/**
 * 三维视域 / 通视分析（考虑高度的真三维）。
 *
 * 从观察点按"方位角 × 俯仰角"发射射线，用 scene.pickFromRay 检测是否被建筑/地形遮挡：
 *   - 不同俯仰角 → 射线有向上/向下分量，能体现高楼对视线的三维遮挡；
 *   - 绿色=可视、红色=被挡，并按可视命中点标出可视边界。
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

/**
 * @param eyeHeight 观察点离地高度（米）
 * @param radius    分析半径（米）
 */
export async function viewshed(viewer, layerManager, observerLonLat, eyeHeight = 30, radius = 600) {
  const [olon, olat] = observerLonLat
  const ground = await sampleGround(viewer, olon, olat)
  const origin = Cesium.Cartesian3.fromDegrees(olon, olat, ground + eyeHeight)

  layerManager.clear('viewshed')
  const ds = layerManager._ds('viewshed')

  // 观察点标记（立柱 + 顶点）
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

  // 本地东-北-上(ENU)坐标系，用于按方位/俯仰构造方向
  const enu = Cesium.Transforms.eastNorthUpToFixedFrame(origin)
  const AZ = 96, PITCHES = [-8, 0, 8, 18]   // 方位数 × 俯仰角（度）
  let visible = 0, total = 0

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
      let end = target, isVis = true
      if (hit && Cesium.defined(hit.position)) {
        const dHit = Cesium.Cartesian3.distance(origin, hit.position)
        if (dHit < radius - 5) { end = hit.position; isVis = false }
      }
      isVis ? visible++ : 0

      ds.entities.add({
        polyline: { positions: [origin, end], width: pdeg === 0 ? 2.2 : 1.4,
          material: isVis ? Cesium.Color.fromBytes(120, 246, 200, 150)
            : Cesium.Color.fromBytes(255, 92, 124, 140) }
      })
    }
  }
  return { visible, blocked: total - visible, total, ratio: +(visible / total).toFixed(2), eyeHeight }
}
