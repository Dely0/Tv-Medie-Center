"""drpyS 侧车进程管理：探测健康、自动拉起、等待就绪。"""
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import config as cfg

logger = logging.getLogger("sidecar")


def node_exe() -> str | None:
    """优先使用项目内便携 Node（D 盘），其次系统 PATH。"""
    if os.path.isdir(cfg.NODE_DIR):
        for entry in os.listdir(cfg.NODE_DIR):
            cand = os.path.join(cfg.NODE_DIR, entry, "node.exe")
            if os.path.exists(cand):
                return cand
    return shutil.which("node")


def is_ready(timeout: float = 2.0) -> bool:
    if not cfg.DRPYS_ENABLED:
        return False
    try:
        req = urllib.request.Request(cfg.DRPYS_BASE_URL.rstrip("/") + "/health",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def start() -> bool:
    """以隐藏窗口方式启动 drpyS（D 盘项目内）。"""
    if not os.path.exists(os.path.join(cfg.DRPYS_DIR, "index.js")):
        logger.warning(f"drpyS 未安装: {cfg.DRPYS_DIR} 下缺少 index.js，请先运行 scripts/setup_drpys.ps1")
        return False
    node = node_exe()
    if not node:
        logger.warning("未找到 Node.js，无法启动 drpyS")
        return False
    try:
        os.makedirs(cfg.DRPYS_LOG_DIR, exist_ok=True)
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(node) + os.pathsep + env.get("PATH", "")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        log = open(os.path.join(cfg.DRPYS_LOG_DIR, "drpys.log"), "a", encoding="utf-8")
        err = open(os.path.join(cfg.DRPYS_LOG_DIR, "drpys.err.log"), "a", encoding="utf-8")
        subprocess.Popen(
            [node, "index.js"],
            cwd=cfg.DRPYS_DIR,
            env=env,
            stdout=log,
            stderr=err,
            creationflags=flags,
            close_fds=True,
        )
        logger.info("drpyS 侧车已启动")
        return True
    except Exception as e:
        logger.warning(f"drpyS 启动失败: {e}")
        return False


def ensure_started(wait_seconds: float = 20.0) -> bool:
    """确保 drpyS 在运行；未运行则拉起并等待就绪。"""
    if not cfg.DRPYS_ENABLED:
        return False
    if is_ready():
        return True
    if not start():
        return False
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_ready():
            logger.info("drpyS 已就绪")
            return True
        time.sleep(0.5)
    logger.warning("drpyS 启动超时，请查看 sidecar/logs/drpys.err.log")
    return False
