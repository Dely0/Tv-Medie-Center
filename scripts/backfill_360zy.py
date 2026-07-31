"""一次性/可复用脚本：把 360 资源（360zy.com）列表页数据轻量回填到本地库。

只写入 videos 表（列表页自带标题/封面/年份等），剧集在用户打开详情页时
由 API 实时从源站拉取并入库。相比全量爬虫，耗时可控。

用法: python scripts/backfill_360zy.py [每类页数]
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.maccms_source import get_manager
from app.database import upsert_video, rebuild_fts


def main():
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    config = os.path.join("data", "maccms_sources.json")
    get_manager().load_from_config(config)
    source = next((s for s in get_manager().get_all() if "360" in s.name), None)
    if not source:
        print("未找到 360 资源源，请检查 data/maccms_sources.json")
        return

    total = 0
    seen = set()
    t_start = time.time()
    for cat in ("movie", "tv", "variety", "anime"):
        for pg in range(1, pages + 1):
            t0 = time.time()
            try:
                items = source.list_page(cat, pg, pagesize=100)
            except Exception as e:
                print(f"[{cat}] p{pg} 失败: {e}")
                continue
            if not items:
                break
            added = 0
            for it in items:
                su = it.get("source_url", "")
                if not su or su in seen:
                    continue
                seen.add(su)
                try:
                    upsert_video(it)
                    added += 1
                except Exception as e:
                    print("upsert 失败:", e)
            total += added
            print(f"[{cat}] p{pg}: {len(items)} 条, 新增 {added}, 耗时 {round(time.time()-t0)}s")
    rebuild_fts()
    print(f"完成，共新增/更新 {total} 条，总耗时 {round(time.time()-t_start)}s")


if __name__ == "__main__":
    main()
