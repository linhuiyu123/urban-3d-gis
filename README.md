# 城市三维可视化与分析平台

一个基于 **Cesium + Vue3 + FastAPI** 的城市三维 WebGIS 平台。以杭州为主要研究区（东京备用），
在三维白模城市底座上提供多种空间分析功能，并接入大模型（DeepSeek）实现自然语言驱动的空间分析。

## 功能总览

| 模块 | 说明 | 对应课程知识点 |
|------|------|----------------|
| 三维白模城市 | Cesium OSM Buildings 全球建筑白模，杭州/东京可切换 | Cesium 三维可视化（应用开发 06-08） |
| 住房/地段价值评估 | 多因子缓冲 + 距离衰减 + 加权叠加，三维价值热力 | 缓冲区/叠置分析（空间分析 Lect.1-2、应用开发 13） |
| 选址分析 | 价值评估反向查询，筛选满足条件的高分地块 | 适宜性分析 |
| 路径规划 | 通勤最优路径 + 灾害撤离路径（就近避难、避险） | 网络分析（导论 Lect.10/15） |
| 热点/空间自相关 | Moran's I 全局自相关 + Getis-Ord 热点 | 空间统计（空间分析 Lect.5/8） |
| 视域/天际线/日照 | 三维通视、天际线、建筑阴影分析 | 三维空间分析 |
| 服务区/等时圈 | 从设施出发 X 分钟可达范围 | 网络分析 |
| 洪水淹没模拟 | 按水位计算淹没范围，并自动重算避险撤离路线 | 栅格分析（导论 Lect.9、应用开发 14） |
| AI 空间分析助手 | 自然语言 → DeepSeek function calling → 调用分析引擎 → 前端联动高亮 | 人工智能与空间分析（导论 Lect.11） |

## 技术栈

- **前端**：Vue 3 + Vite + CesiumJS + Axios
- **后端**：FastAPI + GeoPandas / Shapely + NetworkX + esda/libpysal + NumPy
- **大模型**：DeepSeek（Chat + Function Calling），未配置 Key 时自动降级为 Mock 规则引擎

## 目录结构

```
gis-platform/
├─ frontend/          # Vue3 + Cesium 前端
│  ├─ src/
│  │  ├─ cesium/      # 三维场景、图层、分析可视化
│  │  ├─ components/  # UI 面板组件
│  │  ├─ api/         # 后端接口封装
│  │  └─ styles/      # 玻璃拟态主题样式
│  └─ ...
├─ backend/           # FastAPI 后端
│  ├─ app/
│  │  ├─ routers/     # 各分析接口路由
│  │  ├─ engine/      # 空间分析算法引擎
│  │  └─ config.py
│  └─ requirements.txt
├─ data/              # 杭州样例数据 + 数据更新脚本
└─ docs/              # 技术说明文档
```

## 快速开始（Windows 11 / PowerShell）

> 以下命令均为 Windows PowerShell 写法。请开两个 PowerShell 窗口，一个跑后端、一个跑前端。

### 1. 启动后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

> **请一行一行回车，别把两条命令粘成一行。** 上面直接调用虚拟环境里的 python，免去激活，
> 也能避开 PATH 上其它环境的 `uvicorn.exe` 报 `Fatal error in launcher: Unable to create process`。
> 想激活也可以：`.\.venv\Scripts\Activate.ps1`（若提示“禁止运行脚本”，先
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`；CMD 窗口则用 `.venv\Scripts\activate.bat`）。
>
> 若 `pip install -r requirements.txt` 在 `geopandas` / `fiona` / `osmnx` 上安装失败（Windows 常见），
> 这些**后端运行时并不需要**（只有抓数据脚本 `fetch_data.py` 用到 osmnx），只装核心库即可启动：
> `.\.venv\Scripts\python.exe -m pip install fastapi "uvicorn[standard]" pydantic pydantic-settings python-dotenv numpy scipy shapely networkx pyproj openai`
> 其中 `openai` 用于调用在线 DeepSeek（`backend/.env` 已配置 Key）；不装它 AI 会自动降级为本地规则引擎，
> 热点分析则自动降级为 NumPy 实现——其余功能均不受影响。

后端启动后访问 http://localhost:8000/docs 可看到全部接口文档。

### 2. 启动前端

```powershell
cd frontend
npm install
npm run dev
```

打开浏览器访问 http://localhost:5173 。

### 3. 配置 DeepSeek（可选）

在 `backend/.env` 中填入：

```
DEEPSEEK_API_KEY=你的key
```

不填则 AI 助手自动使用内置 Mock 规则引擎，平台其余功能不受影响。

## 数据说明

平台默认用程序化生成的样例数据，保证**离线开箱即用**。研究区可在顶栏切换：
杭州·主城区 / 都市区 / 全域 + 东京；底图可切换影像注记 / 纯影像 / 街道（均 WGS84 对齐）。

如需真实 OSM 数据（真实路网、POI、水系），按研究区抓取（抓取依赖 `osmnx`、`requests`，
已在 `requirements.txt` 中）：

```powershell
cd data
python fetch_data.py hangzhou_core    # 推荐先抓主城区
python fetch_data.py hangzhou_metro   # 都市区
# python fetch_data.py hangzhou_full  # 全域数据量大、较慢
```

> 抓取成功后会在 `data\<区域>\` 下生成 `pois.geojson` / `water.geojson` / `roads.geojson`，
> **重启后端**即自动优先读取真实数据；抓取失败或未抓取时回退样例，不影响启动。
> 国内访问 Overpass/OSM 可能较慢或需代理。

> 详细的架构、接口与空间分析原理见 `docs/技术说明文档.md`。

## 上传到 GitHub

> 只把 `gis-platform/` 这个工程推到 GitHub（其上层 `gis project/` 里的课程 PDF 体积大，
> 不要一起传）。仓库已带 `.gitignore`，会自动忽略 `.venv/`、`node_modules/`、`backend/.env`、
> `frontend/.env`（含密钥），密钥不会被上传。

在 `gis-platform` 目录下（Windows PowerShell）：

```powershell
cd "C:\Users\lenovo\Claude\Projects\gis project\gis-platform"
git init
git add .
git status                       # 确认 .env、.venv、node_modules 未被加入
git commit -m "init: 城市三维可视化与分析平台"
git branch -M main
```

然后二选一推到远端：

**方式 A：已装 GitHub CLI（gh）——一条命令建库并推送**

```powershell
gh auth login                    # 首次使用先登录
gh repo create urban-3d-gis --public --source . --remote origin --push
```

**方式 B：先在网页 https://github.com/new 建一个空仓库（不要勾 README/.gitignore），再：**

```powershell
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

> 推送时若让你登录：用浏览器授权，或在密码处粘贴 GitHub 的 **Personal Access Token**（GitHub 已不支持账号密码推送）。
> 如果 `git status` 里出现了 `frontend/.env` 或 `backend/.env`，**先不要 commit**，告诉我，我帮你确认忽略规则。
