"""本地批量抓取助手（副本，不改公共 fetch_data.py）。

为什么单独一个文件：fetch_data.py 是团队共享文件，直接改它的 URL 会影响所有人、也容易和别人
的改动冲突。这个副本只在你本地用，**复用 fetch_data 的全部逻辑**（含 User-Agent 头、osmnx/
Overpass 回退、"0 要素不落盘"），只做两件事：
  1) 把 Overpass 换成更宽松的镜像，缓解 429 Too Many Requests；
  2) 在各研究区之间加等待，进一步降低被限流概率。

不需要提交到 GitHub（数据本身也被 .gitignore 忽略）。

用法（在 data 目录下运行）：
    python fetch_data_all.py                 # 抓全部研究区（主城/都市区/全域/东京）
    python fetch_data_all.py tokyo           # 只抓某一个
    python fetch_data_all.py hangzhou_full   # 单独补抓最大的全域

若该镜像在国内也慢/连不上，把下面 OVERPASS_URL 换成其它镜像再跑，例如：
    https://lz4.overpass-api.de/api/interpreter
    https://maps.mail.ru/osm/tools/overpass/api/interpreter
"""
import sys
import time

import fetch_data   # 复用同目录下官方抓取逻辑

# 换成更宽松的 Overpass 镜像（缓解 429）。仅影响本副本，不动 fetch_data.py。
fetch_data.OVERPASS_URL = "https://overpass.kumi.systems/api/interpreter"

WAIT_BETWEEN_AREAS = 10   # 秒：各研究区之间等待，降低限流概率


def main():
    areas = sys.argv[1:] or list(fetch_data.AREAS.keys())
    print(f"使用镜像：{fetch_data.OVERPASS_URL}")
    print(f"将依次抓取：{areas}\n")
    for i, area in enumerate(areas):
        fetch_data.main([area])              # 复用官方逻辑，逐个研究区抓取
        if i < len(areas) - 1:
            print(f"  …等待 {WAIT_BETWEEN_AREAS}s，降低限流概率…\n")
            time.sleep(WAIT_BETWEEN_AREAS)
    print("\n全部完成。重启后端即可使用真实数据。")


if __name__ == "__main__":
    main()
