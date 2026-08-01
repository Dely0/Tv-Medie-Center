"""视频源配置远程更新：拉取仓库中的 maccms_sources.json，校验后替换本地并热加载。

用途：视频源失效需要换源时，只需更新远程配置（本仓库），
已部署的 miniPC 在应用启动或手动触发时即可拉取新源，无需重新发布版本。
"""
import json
import logging
import os
import shutil
import urllib.request

logger = logging.getLogger("source_updater")


def _fetch_remote(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def validate_config(data) -> bool:
    """远程配置格式校验：sources 数组，每项含 name/base_url"""
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        return False
    for s in data["sources"]:
        if not isinstance(s, dict):
            return False
        if not s.get("name") or not s.get("base_url"):
            return False
        cm = s.get("category_map")
        if cm is not None and not isinstance(cm, dict):
            return False
    return True


def update_sources_from_remote(url: str = None) -> dict:
    """拉取远程源配置 → 校验 → 备份替换本地 → 热加载。返回结果字典。"""
    import config as cfg
    from app.maccms_source import get_manager

    url = url or cfg.REMOTE_SOURCES_URL
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "data", "maccms_sources.json")
    cfg_path = os.path.abspath(cfg_path)

    try:
        text = _fetch_remote(url)
        data = json.loads(text)
    except Exception as e:
        return {"success": False, "error": f"远程配置拉取失败: {e}"}

    if not validate_config(data):
        return {"success": False, "error": "远程配置格式无效，已忽略"}

    # 防止远程旧配置覆盖本地新配置：
    # 仅当远程版本号更高，或版本相同但远程源数量更多时更新
    local_data = {"sources": []}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            local_data = json.load(f)
    except Exception:
        pass
    local_ver = int(local_data.get("_version") or 0)
    remote_ver = int(data.get("_version") or 0)
    local_count = len(local_data.get("sources") or [])
    remote_count = len(data.get("sources") or [])
    if remote_ver < local_ver or (remote_ver == local_ver and remote_count <= local_count):
        return {"success": False, "error": "远程配置不新于本地，跳过", "skipped": True}

    try:
        if os.path.exists(cfg_path):
            shutil.copy2(cfg_path, cfg_path + ".bak")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"success": False, "error": f"配置写入失败: {e}"}

    get_manager().load_from_config(cfg_path)
    names = [s.get("name", "") for s in data["sources"]]
    logger.info(f"视频源配置已更新: {names}")
    return {"success": True, "count": len(names), "sources": names}
