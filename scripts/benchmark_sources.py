"""视频源基准测试：连接速度 / 延时 / 下载网速 / 资源完备度。

用法: python scripts/benchmark_sources.py [--adult]
默认只测普通源配置；加 --adult 会包含成人源（结果中单独标注）。
报告输出到 docs/SOURCE_BENCHMARK.md
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def probe_api(base, count=3):
    """API 延时测试：videolist 请求 count 次"""
    url = f"{base}/api.php/provide/vod?ac=videolist&pg=1&pagesize=3&at=json"
    times = []
    ok = 0
    total = None
    first = None
    for _ in range(count):
        t0 = time.time()
        try:
            r = requests.get(url, timeout=15, headers=HEADERS)
            dt = (time.time() - t0) * 1000
            times.append(dt)
            if r.status_code == 200:
                j = r.json()
                if j.get("code") == 1:
                    ok += 1
                    total = j.get("total")
                    lst = j.get("list") or []
                    if lst:
                        first = lst[0].get("vod_name")
        except Exception:
            times.append(None)
        time.sleep(0.3)
    valid = [t for t in times if t is not None]
    return {
        "ok": ok, "attempts": count,
        "avg_ms": round(sum(valid) / len(valid), 1) if valid else None,
        "min_ms": round(min(valid), 1) if valid else None,
        "max_ms": round(max(valid), 1) if valid else None,
        "total": total, "first": first,
    }


def probe_categories(base, cat_map):
    """分类完备度：每个分类 ID 的返回数量"""
    result = {}
    for cat, cat_id in (cat_map or {}).items():
        if not cat_id:
            continue
        ids = [c.strip() for c in str(cat_id).split(",") if c.strip()]
        total = 0
        samples = 0
        for cid in ids[:4]:
            try:
                url = f"{base}/api.php/provide/vod?ac=videolist&t={cid}&pg=1&pagesize=3&at=json"
                r = requests.get(url, timeout=12, headers=HEADERS)
                j = r.json()
                total += int(j.get("total") or 0)
                samples += len(j.get("list") or [])
            except Exception:
                continue
        result[cat] = {"ids": len(ids), "total": total, "sample": samples}
    return result


def probe_field_quality(base):
    """字段完备度：列表页前 10 条的字段完整率"""
    url = f"{base}/api.php/provide/vod?ac=videolist&pg=1&pagesize=10&at=json"
    fields = {"vod_pic": 0, "vod_content": 0, "vod_play_url": 0, "vod_actor": 0,
              "vod_score": 0, "vod_year": 0, "vod_remarks": 0}
    total_items = 0
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        lst = (r.json().get("list") or [])[:10]
        total_items = len(lst)
        for v in lst:
            for f in fields:
                val = v.get(f)
                if val not in (None, "", 0, "0"):
                    fields[f] += 1
    except Exception:
        pass
    return {"items": total_items,
            "rates": {k: (round(v / total_items * 100) if total_items else 0) for k, v in fields.items()}}


def resolve_ts(url, referer=None, max_depth=4):
    """解析 m3u8 到第一个真实分片 URL"""
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    try:
        r = requests.get(url, timeout=20, headers=h)
        if r.status_code != 200:
            return None, f"master HTTP {r.status_code}"
        rels = [l for l in r.text.splitlines() if l and not l.startswith("#")]
        if not rels:
            return None, "master empty"
        rel = rels[0]
        if rel.startswith("/"):
            from urllib.parse import urljoin
            u = urljoin(url, rel)
        elif rel.startswith("http"):
            u = rel
        else:
            u = url.rsplit("/", 1)[0] + "/" + rel
        if ".m3u8" in u:
            if max_depth <= 0:
                return None, "too deep"
            return resolve_ts(u, referer, max_depth - 1)
        return u, None
    except Exception as e:
        return None, str(e)[:60]


def probe_cdn(url, referer=None):
    """CDN 下载速度：TTFB + 前 1MB 下载速度（3 个分片取均值）"""
    if not url or not url.startswith("http"):
        return None
    seg_url, err = resolve_ts(url, referer)
    if not seg_url:
        return {"error": f"解析失败: {err}"}
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    speeds = []
    ttfb_list = []
    last_err = "未知"
    for _ in range(3):
        try:
            t0 = time.time()
            rr = requests.get(seg_url, timeout=25, headers=h, stream=True)
            t1 = time.time()
            got = 0
            for chunk in rr.iter_content(65536):
                got += len(chunk)
                if got >= 1048576:
                    break
            t2 = time.time()
            dt = max(t2 - t0, 0.001)
            speeds.append(got / dt / 1024)
            ttfb_list.append((t1 - t0) * 1000)
        except Exception as e:
            speeds.append(0)
            ttfb_list.append(None)
            last_err = str(e)[:70]
        time.sleep(0.2)
    valid = [s for s in speeds if s > 0]
    result = {
        "host": seg_url.split("/")[2],
        "avg_kbs": round(sum(valid) / len(valid)) if valid else 0,
        "min_kbs": round(min(valid)) if valid else 0,
        "max_kbs": round(max(valid)) if valid else 0,
        "ttfb_ms": round(sum(t for t in ttfb_list if t is not None) / len([t for t in ttfb_list if t is not None])) if any(ttfb_list) else None,
    }
    if not valid:
        result["note"] = f"分片下载全部失败: {last_err}"
    return result


def probe_episodes(base):
    """剧集完备度：取列表第一个视频详情，解析集数"""
    try:
        url = f"{base}/api.php/provide/vod?ac=videolist&pg=1&pagesize=1&at=json"
        j = requests.get(url, timeout=12, headers=HEADERS).json()
        lst = j.get("list") or []
        if not lst:
            return {"error": "列表为空"}
        vid = lst[0].get("vod_id")
        url2 = f"{base}/api.php/provide/vod?ac=detail&ids={vid}&at=json"
        j2 = requests.get(url2, timeout=12, headers=HEADERS).json()
        items = j2.get("list") or []
        if not items:
            return {"error": "详情为空"}
        pu = (items[0].get("vod_play_url") or "")
        n_sources = pu.count("$$$") + 1 if pu else 0
        eps = len([p for p in pu.split("#") if "$" in p]) if pu else 0
        return {"title": str(items[0].get("vod_name"))[:20], "episodes": eps, "play_sources": n_sources}
    except Exception as e:
        return {"error": str(e)[:60]}


def probe_search(base):
    """搜索可用性：搜一个常见词"""
    try:
        import urllib.parse
        url = f"{base}/api.php/provide/vod?ac=videolist&wd={urllib.parse.quote('电影')}&pagesize=3&at=json"
        j = requests.get(url, timeout=12, headers=HEADERS).json()
        total = int(j.get("total") or 0)
        return {"ok": j.get("code") == 1, "total": total}
    except Exception:
        return {"ok": False, "error": "请求失败"}


def benchmark_source(name, base, cat_map, is_adult=False):
    result = {"name": name, "base_url": base, "adult": is_adult}
    result["api"] = probe_api(base)
    if result["api"]["ok"] == 0:
        result["error"] = "API 不可用"
        return result
    result["categories"] = probe_categories(base, cat_map)
    result["fields"] = probe_field_quality(base)
    result["episodes"] = probe_episodes(base)
    result["search"] = probe_search(base)
    # 播放 CDN：从列表取第一个播放地址
    try:
        url = f"{base}/api.php/provide/vod?ac=videolist&pg=1&pagesize=5&at=json"
        lst = requests.get(url, timeout=15, headers=HEADERS).json().get("list") or []
        play_url = ""
        for v in lst:
            pu = v.get("vod_play_url") or ""
            if pu:
                ep = pu.split("#")[0]
                play_url = ep.split("$")[1] if "$" in ep else ep
                break
        result["cdn"] = probe_cdn(play_url, referer=base + "/")
    except Exception as e:
        result["cdn"] = {"error": str(e)[:60]}
    return result


def score_source(r):
    """综合评分（0-100）：API 延时 30% + CDN 速度 40% + 资源量 15% + 字段完备 15%"""
    score = 0
    api = r.get("api") or {}
    if api.get("ok"):
        avg = api.get("avg_ms") or 9999
        score += 30 * max(0, 1 - avg / 8000)
    cdn = r.get("cdn") or {}
    sp = cdn.get("avg_kbs") or 0
    score += 40 * min(1, sp / 800)
    total = api.get("total") or 0
    score += 15 * min(1, total / 100000)
    fields = r.get("fields") or {}
    rates = fields.get("rates") or {}
    score += 15 * ((rates.get("vod_pic") or 0) / 100)
    return round(score, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adult", action="store_true", help="包含成人源")
    args = parser.parse_args()

    with open(os.path.join("data", "maccms_sources.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    sources = [(s["name"], s["base_url"], s.get("category_map"), False)
               for s in cfg.get("sources", []) if s.get("enabled", True)]

    if args.adult:
        try:
            with open(os.path.join("data", "adult_config.json"), "r", encoding="utf-8") as f:
                adult_cfg = json.load(f)
            # --adult 时强制测试配置中的成人源（无论开关状态），报告中单独标注
            for s in adult_cfg.get("sources", []):
                sources.append((s["name"], s["base_url"], s.get("category_map"), True))
        except FileNotFoundError:
            print("未找到 adult_config.json，跳过成人源")

    print(f"开始测试 {len(sources)} 个源...")
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(benchmark_source, n, b, c, a): n for n, b, c, a in sources}
        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            status = "OK" if "error" not in r else f"FAIL({r['error'][:30]})"
            print(f"  [{status}] {r['name']}")

    # 生成 Markdown 报告
    results.sort(key=score_source, reverse=True)
    lines = []
    lines.append("# 视频源接口测试报告")
    lines.append("")
    lines.append(f"- 测试时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 测试源数量：{len(results)}")
    lines.append("- 测试维度：API 连接速度/延时、CDN 下载（缓冲）网速、资源完备度（总量/分类/字段/剧集/搜索）")
    lines.append("- 说明：成人源单独标注，默认不加载；速度受本机网络与源站 CDN 节点影响，结果供相对参考")
    lines.append("")

    lines.append("## 综合排名")
    lines.append("")
    lines.append("| 排名 | 源 | 综合分 | API平均延时 | CDN平均速度 | 资源总量 | 封面完整率 | 备注 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(results, 1):
        api = r.get("api") or {}
        cdn = r.get("cdn") or {}
        fields = r.get("fields") or {}
        rates = fields.get("rates") or {}
        remark = "成人源" if r["adult"] else ("不可用" if "error" in r else "")
        lines.append(
            f"| {i} | {r['name']} | {score_source(r)} | "
            f"{api.get('avg_ms', '-')} ms | {cdn.get('avg_kbs', '-')} KB/s | "
            f"{api.get('total', '-')} | {rates.get('vod_pic', 0)}% | {remark} |"
        )
    lines.append("")

    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r['name']}" + ("（成人源）" if r["adult"] else ""))
        lines.append("")
        lines.append(f"- 接口地址：`{r['base_url']}/api.php/provide/vod`")
        if "error" in r:
            lines.append(f"- **状态：不可用**（{r['error']}）")
            lines.append("")
            continue
        api = r["api"]
        lines.append("- **API 连接/延时**（videolist 测 3 次）")
        lines.append(f"  - 成功率：{api['ok']}/{api['attempts']}")
        lines.append(f"  - 平均延时：{api['avg_ms']} ms（最小 {api['min_ms']} / 最大 {api['max_ms']}）")
        lines.append(f"  - 资源总量：{api['total']} 部")
        lines.append(f"  - 最新一条：{api['first']}")
        lines.append("")
        lines.append("- **分类覆盖**")
        for cat, info in (r.get("categories") or {}).items():
            lines.append(f"  - {cat}：{info['total']} 部（配置 {info['ids']} 个分类ID，抽查 {info['sample']} 条）")
        lines.append("")
        lines.append("- **字段完备度**（列表前 10 条完整率）")
        rates = r["fields"]["rates"]
        lines.append("  - " + "、".join(f"{k.replace('vod_','')}: {v}%" for k, v in rates.items()))
        lines.append("")
        ep = r.get("episodes") or {}
        lines.append("- **详情/剧集**：")
        if "error" in ep:
            lines.append(f"  - 解析失败：{ep['error']}")
        else:
            lines.append(f"  - 示例：《{ep.get('title')}》共 {ep.get('episodes')} 集，播放源 {ep.get('play_sources')} 个")
        lines.append("")
        sr = r.get("search") or {}
        lines.append(f"- **搜索可用性**：{'可用（返回 ' + str(sr.get('total')) + ' 条）' if sr.get('ok') else '不可用'}")
        lines.append("")
        cdn = r.get("cdn") or {}
        lines.append("- **CDN 下载（缓冲）速度**（3 个分片取均值）")
        if "error" in cdn:
            lines.append(f"  - 测试失败：{cdn['error']}")
        elif cdn.get("note"):
            lines.append(f"  - 平均速度：{cdn.get('avg_kbs')} KB/s（全部失败，{cdn['note']}）")
            lines.append(f"  - CDN 节点：`{cdn.get('host')}`")
        else:
            lines.append(f"  - CDN 节点：`{cdn.get('host')}`")
            lines.append(f"  - 平均速度：{cdn.get('avg_kbs')} KB/s（最小 {cdn.get('min_kbs')} / 最大 {cdn.get('max_kbs')}）")
            lines.append(f"  - 首字节延时：{cdn.get('ttfb_ms')} ms")
            lines.append(f"  - 参考：2Mbps 码率视频约需 250 KB/s")
        lines.append("")

    os.makedirs("docs", exist_ok=True)
    out_path = os.path.join("docs", "SOURCE_BENCHMARK.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已生成: {out_path}")


if __name__ == "__main__":
    main()
