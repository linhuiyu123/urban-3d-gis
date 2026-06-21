# 洪水分析模块与 AI 助手模块说明

> 本文档面向课程汇报、代码答辩和后续维护，说明平台中“洪水淹没分析”和“AI 空间分析助手”两个模块的设计目标、数据输入、算法流程、接口参数、前端联动方式与使用注意事项。

---

## 1. 洪水淹没分析模块

### 1.1 模块定位

洪水淹没分析模块用于在指定研究区内，根据用户设置的水位高度，模拟河流、湖泊、海岸线等水体向周边低洼区域连通扩散形成的淹没范围。模块输出的危险区多边形不仅用于地图可视化，也会作为撤离路径规划的避让障碍，实现“淹没分析 → 风险区生成 → 撤离路径重算”的灾害应急闭环。

当前支持的研究区包括：

| 研究区 ID        | 名称        | 数据目录               |
| ---------------- | ----------- | ---------------------- |
| `hangzhou_core`  | 杭州·主城区 | `data/hangzhou_core/`  |
| `hangzhou_metro` | 杭州·都市区 | `data/hangzhou_metro/` |
| `hangzhou_full`  | 杭州·全域   | `data/hangzhou_full/`  |
| `tokyo`          | 东京        | `data/tokyo/`          |

前端切换研究区后，洪水接口会收到对应的 `city` 参数，后端按 `data/<city>/water.geojson` 和 `data/<city>/dem.npy` 读取该区域的水系与高程数据。

### 1.2 数据输入

洪水模块主要依赖两类数据：

| 文件                        | 作用                                     | 缺失时处理               |
| --------------------------- | ---------------------------------------- | ------------------------ |
| `data/<city>/water.geojson` | 水体种子，包括河流、湖泊、水库、海岸线等 | 自动回退为程序化样例水系 |
| `data/<city>/dem.npy`       | 规则格网 DEM，高程单位为米               | 自动使用合成地形场       |

`water.geojson` 可通过 OSM / Overpass 抓取：

```powershell
python data\fetch_data.py --water-only hangzhou_metro
```

`dem.npy` 可由 GeoTIFF DEM 转换得到：

```powershell
python data\prepare_dem.py hangzhou_metro data\hangzhou_metro\dem.tif --resolution 100
```

对于杭州都市区，GeoTIFF 可从 OpenTopography 下载后保存为：

```text
data/hangzhou_metro/dem.tif
```

### 1.3 核心算法

洪水模块位于：

```text
backend/app/engine/flood.py
```

算法不是简单地把所有低于水位的格子判定为淹没，而是采用“水体起涨 + 地形约束 + 连通扩散”的模拟思路。

计算流程如下：

1. **构建规则格网**

   根据研究区 bbox 和前端传入的 `resolution` 生成 `n × n` 规则格网。格网坐标先以 WGS84 表示，再投影到 UTM 米制坐标，用于距离、面积和缓冲计算。

2. **读取并融合水系**

   后端读取 `water.geojson`，将河流、湖泊、海岸线等要素投影到 UTM 坐标系。线状水体会先做适度 buffer，转为具有面积的水体带；多个水体通过 `unary_union` 融合为统一水系几何。

3. **读取真实 DEM 或生成合成地形**
   - 若存在 `dem.npy`，则直接读取真实高程；
   - 若 DEM 分辨率与本次请求的 `resolution` 不一致，会重采样到当前格网大小；
   - 若 DEM 缺失或无效，则使用合成地形场。合成地形会模拟“西北较高、东南较低、近水体地势较低、离水体越远地势缓慢抬升”的趋势。

4. **确定起涨种子格**

   只有水体范围内或最接近水体的格子会作为洪水起涨种子。这样可以避免“全区最低点”凭空积水，也避免固定宽度种子带盖过水位参数。

5. **计算水面高度**

   用户输入的 `water_level` 被理解为相对水体基准面的涨水高度。模块会取水体种子格高程的低分位作为局部水位基准：

   ```text
   surface_elevation = water_datum + water_level
   ```

   这样可以适配不同 DEM 的绝对高程基准，避免同样 6m 水位在不同数据源中失真。

6. **连通扩散**

   对所有低于当前水面高度的格子做候选筛选，但最终只保留“与水体种子连通”的候选格。内陆孤立低洼区如果不与水体相连，不会被判定为淹没。

7. **面化和平滑**

   淹没格会先压缩为矩形面片，再融合为统一危险区。随后在 UTM 米制坐标下做平滑 buffer 和拓扑简化，最终转回 WGS84 GeoJSON 返回前端。

### 1.4 接口说明

单次洪水模拟接口：

```http
POST /api/flood
```

请求体：

```json
{
  "city": "hangzhou_metro",
  "water_level": 6,
  "resolution": 100
}
```

主要参数：

| 参数          | 类型    | 说明                                  |
| ------------- | ------- | ------------------------------------- |
| `city`        | string  | 研究区 ID，如 `hangzhou_metro`        |
| `water_level` | number  | 水位或涨水高度，单位米                |
| `resolution`  | integer | 淹没格网分辨率，例如 100 表示 100×100 |

涨水动画接口：

```http
POST /api/flood/animation
```

请求体：

```json
{
  "city": "hangzhou_metro",
  "target_level": 8,
  "frames": 9,
  "resolution": 90
}
```

该接口会从低水位到目标水位生成多个淹没帧，前端逐帧渲染形成涨水过程动画。

### 1.5 返回结果

洪水接口返回结构包括：

| 字段                          | 说明                                                |
| ----------------------------- | --------------------------------------------------- |
| `surface`                     | GeoJSON FeatureCollection，用于前端渲染蓝色淹没水面 |
| `hazard`                      | GeoJSON Geometry，用于撤离路径避让                  |
| `meta.area`                   | 本次分析使用的研究区 ID                             |
| `meta.water_level`            | 输入水位                                            |
| `meta.flooded_area_km2`       | 淹没面积，单位 km²                                  |
| `meta.flooded_cells`          | 被淹没的格网单元数量                                |
| `meta.resolution`             | 分析分辨率                                          |
| `meta.elevation_source`       | 高程来源，如 `dem.npy`、`synthetic`                 |
| `meta.elevation_source_label` | 前端展示用中文说明                                  |
| `meta.dem_path`               | 后端尝试读取的 DEM 路径                             |
| `meta.water_datum_m`          | 水体局部基准高程                                    |
| `meta.surface_elevation_m`    | 计算得到的水面高程                                  |
| `meta.warnings`               | DEM 无效值、回退等警告                              |

前端结果面板会显示研究区、水位、淹没面积、分辨率、地形来源、水位基准和水面高程，便于判断是否正确使用了当前区域数据。

### 1.6 前端联动

前端入口位于：

```text
frontend/src/App.vue
frontend/src/components/ControlPanel.vue
frontend/src/components/ResultPanel.vue
frontend/src/cesium/layers.js
```

主要交互流程：

1. 用户在控制面板选择“洪水淹没”模块；
2. 设置水位与分辨率；
3. 点击“模拟淹没”或“涨水过程动画”；
4. 前端调用 `/api/flood` 或 `/api/flood/animation`；
5. `LayerManager.renderFlood()` 将返回的 GeoJSON 渲染为蓝色水面；
6. 若用户已设置撤离起点，前端会把 `hazard` 传给撤离接口，自动重算避开淹没区的撤离路线。

### 1.7 使用建议与限制

- `resolution` 越高，淹没边界越细，但计算越慢。课程演示建议使用 `100` 左右。
- DEM 分辨率最好与常用洪水分辨率接近，例如 `prepare_dem.py --resolution 100`。
- 若结果面板显示“合成地形”，说明当前区域没有读到有效 `dem.npy`。
- 若淹没范围明显不真实，优先检查 `water.geojson` 是否为当前研究区真实水系。
- OSM 水系不等同于专业水文数据，结果适合课程演示和空间分析流程表达，不应作为真实防汛决策依据。

---

## 2. AI 空间分析助手模块

### 2.1 模块定位

AI 空间分析助手是平台的自然语言入口。用户可以不用手动选择模块和填写全部参数，而是直接输入类似：

```text
使用杭州都市区模拟 8 米水位淹没
筛选前 10 个高价值地块
从我选的点规划撤离路线
分析学校因子的热点集聚区
```

后端会将自然语言解析为具体空间分析工具调用，执行分析后把结果图层返回前端，实现“自然语言 → 空间分析 → 地图联动高亮”。

模块代码位于：

```text
backend/app/engine/ai_agent.py
frontend/src/components/AIChat.vue
```

### 2.2 运行模式

AI 助手支持两种模式：

| 模式              | 触发条件                                                     | 说明                                       |
| ----------------- | ------------------------------------------------------------ | ------------------------------------------ |
| DeepSeek 在线模式 | `backend/.env` 配置 `DEEPSEEK_API_KEY`，且 `openai` 依赖可用 | 使用大模型 function calling 判断工具和参数 |
| Mock 规则模式     | 未配置 Key、依赖缺失、网络异常、在线调用失败                 | 使用本地关键词规则解析，保证演示可用       |

这种设计保证了平台不依赖外部模型服务也能完成课堂演示。即使 DeepSeek 调用失败，后端也会优雅降级为本地规则引擎，而不是返回 500 错误。

### 2.3 工具注册

AI 助手将平台已有分析能力注册为工具。每个工具都对应后端已有的分析引擎：

| 工具名           | 对应模块          | 主要参数                             |
| ---------------- | ----------------- | ------------------------------------ |
| `assess_value`   | 地段价值评估      | `city, resolution`                   |
| `site_selection` | 选址分析          | `city, min_score, top_k, resolution` |
| `route`          | 通勤路径规划      | `city, start, end, optimize, mode`   |
| `evacuate`       | 撤离路径规划      | `city, start, mode`                  |
| `hotspot`        | 热点 / 空间自相关 | `city, attr, k`                      |
| `isochrone`      | 服务区 / 等时圈   | `city, center, bands, mode`          |
| `flood`          | 洪水淹没模拟      | `city, water_level, resolution`      |

DeepSeek 在线模式下，工具信息会通过 function calling schema 发送给模型，由模型选择工具并生成参数。Mock 模式下，后端用关键词和数字提取规则选择工具。

### 2.4 城市与参数识别

AI 助手支持常见区域别名：

| 用户说法                   | 解析结果         |
| -------------------------- | ---------------- |
| 杭州、杭州市、主城、主城区 | `hangzhou_core`  |
| 杭州都市区、都市区         | `hangzhou_metro` |
| 杭州全域、全域             | `hangzhou_full`  |
| 东京、tokyo                | `tokyo`          |

城市识别会优先匹配更具体、更长的名称。例如“杭州都市区”会先匹配为 `hangzhou_metro`，不会被“杭州”提前截断为 `hangzhou_core`。

参数识别规则包括：

- 文本中出现“淹没、洪水、内涝、水位、涨水”时，倾向选择 `flood`；
- 文本中出现“撤离、逃生、避难、疏散”时，倾向选择 `evacuate`；
- 文本中出现“热点、聚集、自相关、Moran”时，倾向选择 `hotspot`；
- 文本中出现“等时、服务区、分钟、可达”时，倾向选择 `isochrone`；
- 文本中出现“前 10 个、Top、筛选、高价值地块”时，倾向选择 `site_selection`；
- 文本中的数字会按语义用于水位、Top-K、阈值、分钟档位或近邻数量；
- 用户提到“我选的点、当前点、这里”时，前端会把地图点击坐标作为 `context_point` 传给后端。

### 2.5 多轮上下文

AI 助手维护简单的会话状态：

```text
SESSIONS    DeepSeek 对话历史
MOCK_STATE  Mock 模式下的上一轮工具和参数
```

因此用户可以连续追问：

```text
模拟杭州都市区 6 米水位淹没
再把水位调到 10 米
换成快速分辨率
```

后端会复用上一轮的工具和区域，只更新新的参数。

### 2.6 接口说明

AI 助手接口：

```http
POST /api/ai
```

请求体：

```json
{
  "prompt": "使用杭州都市区模拟 8 米水位淹没",
  "city": "hangzhou_metro",
  "context_point": null,
  "session_id": "default"
}
```

主要参数：

| 参数            | 类型       | 说明                         |
| --------------- | ---------- | ---------------------------- |
| `prompt`        | string     | 用户自然语言输入             |
| `city`          | string     | 当前前端研究区，作为默认区域 |
| `context_point` | array/null | 地图点选坐标 `[lon, lat]`    |
| `session_id`    | string     | 前端会话 ID，用于多轮上下文  |

返回结构：

| 字段     | 说明                                     |
| -------- | ---------------------------------------- |
| `reply`  | AI 或 Mock 生成的中文回复                |
| `tool`   | 实际调用的工具名                         |
| `args`   | 实际传入工具的参数                       |
| `engine` | `deepseek`、`mock` 或 `mock(降级)`       |
| `steps`  | 执行步骤说明，用于前端展开查看           |
| `kind`   | 结果类型，如 `flood`、`site`、`route`    |
| `layer`  | GeoJSON 或分析结果对象，前端据此渲染图层 |

重置会话接口：

```http
POST /api/ai/reset?session_id=default
```

该接口会清空指定会话的 DeepSeek 历史和 Mock 上下文。

### 2.7 前端交互

前端 AI 对话框位于右下角，由 `AIChat.vue` 实现。交互流程为：

1. 用户输入自然语言或点击预设问题；
2. 前端发送 `prompt + 当前 city + context_point + session_id` 到 `/api/ai`；
3. 后端返回工具、参数、结果图层和回复；
4. 前端根据 `kind` 分发渲染：
   - `flood` 调用 `renderFlood()`；
   - `route` / `evacuate` 调用 `renderRoute()`；
   - `value` / `site` 调用 `renderValueGrid()`；
   - `hotspot` 调用 `renderHotspot()`；
   - `isochrone` 调用 `renderIsochrone()`；
5. 结果面板同步显示本次分析元数据。

前端还会展示执行步骤，例如：

```text
理解需求与上下文
选择工具：洪水淹没模拟
调用参数：{"city":"hangzhou_metro","water_level":8,"resolution":100}
执行空间分析引擎
生成回答并联动地图
```

### 2.8 配置方式

DeepSeek 在线模式需要在 `backend/.env` 中配置：

```env
DEEPSEEK_API_KEY=你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

后端依赖中建议安装：

```powershell
python -m pip install openai httpx
```

如果未安装或网络不可用，AI 模块会自动降级为 Mock 模式。

### 2.9 示例

示例 1：洪水淹没

```text
使用杭州都市区模拟 8 米水位淹没
```

解析结果：

```json
{
  "tool": "flood",
  "args": {
    "city": "hangzhou_metro",
    "water_level": 8,
    "resolution": 100
  }
}
```

示例 2：选址分析

```text
筛选前 10 个高价值地块
```

解析结果：

```json
{
  "tool": "site_selection",
  "args": {
    "city": "当前前端研究区",
    "top_k": 10,
    "min_score": 60
  }
}
```

示例 3：基于点选的撤离

```text
从我选的点规划撤离路线
```

解析结果：

```json
{
  "tool": "evacuate",
  "args": {
    "city": "当前前端研究区",
    "start": "context_point"
  }
}
```

### 2.10 使用建议与限制

- AI 助手本质是分析入口，不替代后端空间分析引擎；最终结果仍由本地 Python 分析模块计算。
- 在线模型可能因网络、Key、依赖版本变化不可用，因此保留 Mock 降级路径是必要的。
- 对需要精确坐标的分析，建议先在地图点选位置，再在 AI 中说“从我选的点……”。
- 对复杂决策建议拆成多轮，例如先做淹没，再做撤离，再分析周边服务区。
- AI 生成的解释只用于辅助理解，空间计算结果以返回的 GeoJSON 图层和 `meta` 字段为准。

---

## 3. 两个模块的联动价值

洪水分析模块和 AI 助手模块结合后，可以形成更自然的灾害应急分析流程：

```text
用户自然语言提出情景
        ↓
AI 解析为洪水分析工具和参数
        ↓
洪水模块生成淹没危险区
        ↓
前端渲染水面并保存 hazard
        ↓
撤离模块避开 hazard 重算路线
        ↓
AI 用自然语言解释结果
```

这种设计体现了平台的核心特点：底层仍然是可解释、可复现的 GIS 空间分析算法，上层通过 AI 降低操作门槛，把多个分析模块组织成面向实际问题的工作流。
