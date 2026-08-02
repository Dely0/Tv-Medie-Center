"""drpyS 侧车进程管理：探测健康、自动拉起、等待就绪。"""
import logging
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

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
    """drpyS 健康：HTTP 200 且 Python 守护进程在线（守护进程被杀后视为未就绪）。"""
    if not cfg.DRPYS_ENABLED:
        return False
    try:
        req = urllib.request.Request(cfg.DRPYS_BASE_URL.rstrip("/") + "/health",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            try:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            except Exception:
                return True  # 老版本无 JSON 也视为就绪
            py = data.get("python") or {}
            if py.get("daemon_running") is False:
                return False
            return True
    except Exception:
        return False


def _port_pids(port: int) -> list[int]:
    """返回占用指定端口的进程 PID（Windows netstat）。"""
    try:
        proc = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=10)
        out = (proc.stdout or b"").decode("utf-8", "replace")
    except Exception:
        return []
    if not out:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING":
            local = parts[1]
            if local.rsplit(":", 1)[-1] == str(port):
                try:
                    pid = int(parts[-1])
                except ValueError:
                    continue
                if pid not in pids:
                    pids.append(pid)
    return pids


def restart() -> bool:
    """重启 drpyS：先结束旧 node 与 Python 守护进程，再重新拉起。"""
    port = urlparse(cfg.DRPYS_BASE_URL).port or 5757
    pids = set(_port_pids(port))
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*t4_daemon.py*' } | ForEach-Object { $_.ProcessId }",
            ],
            capture_output=True, timeout=15,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace")
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.isdigit():
                pids.add(int(ln))
    except Exception:
        pass
    for pid in pids:
        if pid == os.getpid():
            continue
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        except Exception:
            pass
    time.sleep(1.0)
    return start()


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
    logger.warning("drpyS 健康检查异常（HTTP 或 Python 守护进程），尝试重启侧车")
    try:
        ok = restart()
    except Exception as e:
        logger.warning(f"drpyS 重启失败: {e}")
        ok = False
    if not ok:
        return False
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_ready():
            logger.info("drpyS 已就绪")
            return True
        time.sleep(0.5)
    logger.warning("drpyS 启动超时，请查看 sidecar/logs/drpys.err.log")
    return False
