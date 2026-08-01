"""同步豆瓣热播榜到本地：抓 4 类榜单 → 匹配/入库 → 写入 douban_ranks 表并回填评分。

用法: python scripts/sync_douban.py [每类条数，默认30]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import init_db
from app.douban import sync_douban_hot


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    init_db()
    t0 = time.time()
    stats = sync_douban_hot(limit_per_category=limit)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"耗时 {round(time.time()-t0)}s")


if __name__ == "__main__":
    main()
