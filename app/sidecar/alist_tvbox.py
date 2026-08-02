"""alist-tvbox 侧车进程管理：AList（网盘驱动）+ alist-tvbox（TVBox 聚合/本地代理）。

部署结构（全部在 D 盘 sidecar 内）：
- sidecar/alist/          AList Windows 版（端口 5244，仅本机监听）
- sidecar/alist-tvbox/    alist-tvbox jar（端口 4567，仅本机监听）
- sidecar/jre21/          OpenJDK 21

alist-tvbox 在 Windows 上硬编码使用 \\opt\\atv\\ 根路径（相对当前盘符根目录），
因此需要 D:\\opt\\atv\\ 下的目录联接（junction）指向 sidecar 实际目录。
"""
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
import zipfile

import config as cfg

logger = logging.getLogger("sidecar_atv")


def _alist_exe() -> str:
    return os.path.join(cfg.ALIST_DIR, "alist.exe")


def _java_exe() -> str:
    return os.path.join(cfg.ATV_JRE_DIR, "bin", "java.exe")


def _h2_jar() -> str:
    return os.path.join(cfg.ATV_DIR, "h2tmp", "h2.jar")


def _alist_ready(timeout: float = 2.0) -> bool:
    try:
        req = urllib.request.Request(cfg.ALIST_TVBOX_ALIST_URL.rstrip("/") + "/ping",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _atv_ready(timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(cfg.ALIST_TVBOX_BASE_URL.rstrip("/") + "/sub/0",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_junctions():
    """Windows 目录联接：D:\\opt\\atv\\* -> sidecar 实际目录（alist-tvbox 硬编码路径）。"""
    if sys.platform != "win32":
        return
    root = "D:\\opt\\atv"
    targets = {
        "alist": cfg.ALIST_DIR,
        "index": os.path.join(cfg.ATV_DIR, "index"),
        "log": os.path.join(cfg.ATV_DIR, "log"),
        "www": os.path.join(cfg.ATV_DIR, "www"),
        "config": os.path.join(cfg.ATV_DIR, "config"),
    }
    for name, target in targets.items():
        link = os.path.join(root, name)
        os.makedirs(target, exist_ok=True)
        try:
            if os.path.islink(link) or os.path.exists(link):
                continue
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null",
                ],
                capture_output=True, timeout=30,
            )
            logger.info("created junction %s -> %s", link, target)
        except Exception as e:
            logger.warning("create junction %s failed: %s", link, e)


def _extract_h2_jar():
    """从 fat jar 里提取 H2 驱动，用于首次启动时修正 enabled_token 设置。"""
    if os.path.exists(_h2_jar()) or not os.path.exists(cfg.ATV_JAR):
        return os.path.exists(_h2_jar())
    try:
        os.makedirs(os.path.dirname(_h2_jar()), exist_ok=True)
        with zipfile.ZipFile(cfg.ATV_JAR) as z:
            for name in z.namelist():
                if name.startswith("BOOT-INF/lib/h2-") and name.endswith(".jar"):
                    with open(_h2_jar(), "wb") as f:
                        f.write(z.read(name))
                    return True
    except Exception as e:
        logger.warning("extract h2 jar failed: %s", e)
    return False


def _fix_enabled_token():
    """把 alist-tvbox 的 enabled_token 设为 false（本地仅本机监听，TVBox 接口免 token）。"""
    db = cfg.ATV_H2_DB
    if not os.path.exists(db + ".mv.db"):
        return
    if not _extract_h2_jar():
        return
    sql = os.path.join(cfg.ATV_DIR, "h2tmp", "fix_token.sql")
    try:
        with open(sql, "w", encoding="ascii") as f:
            f.write('UPDATE SETTING SET "svalue"=\'false\' WHERE name=\'enabled_token\';\n')
        subprocess.run(
            [
                _java_exe(), "-cp", _h2_jar(), "org.h2.tools.RunScript",
                "-url", "jdbc:h2:file:" + db.replace("\\", "/"),
                "-user", "sa", "-password", "password",
                "-script", sql,
            ],
            capture_output=True, timeout=60,
        )
    except Exception as e:
        logger.warning("fix enabled_token failed: %s", e)


def _ensure_quark_storage():
    """幂等：把 drpyS env.json 里的夸克 Cookie 挂到 AList（/quark），并开启转码直链。
    仅在 AList 未运行时调用（避免与 AList 自身的 SQLite 写入冲突）。"""
    db = os.path.join(cfg.ALIST_DATA_DIR, "data.db")
    if not os.path.exists(db):
        return
    try:
        import datetime
        import sqlite3
        env_path = os.path.join(cfg.DRPYS_DIR, "config", "env.json")
        if not os.path.exists(env_path):
            return
        env = json.load(open(env_path, encoding="utf-8"))
        ck = env.get("quark_cookie") or ""
        if not ck:
            return
        addition = json.dumps({
            "cookie": ck,
            "root_folder_id": "0",
            "order_by": "none",
            "order_direction": "asc",
            "use_transcoding_address": True,   # 夸克转码直链，mkv 也能转 mp4 播放
            "only_list_video_file": False,
        }, ensure_ascii=False)
        con = sqlite3.connect(db)
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM x_storages WHERE mount_path='/quark'")
        if cur.fetchone()[0]:
            cur.execute("SELECT addition FROM x_storages WHERE mount_path='/quark'")
            row = cur.fetchone()
            if row and row[0] and '"use_transcoding_address":true' not in row[0]:
                cur.execute("UPDATE x_storages SET addition=?, modified=? WHERE mount_path='/quark'",
                            (addition, datetime.datetime.now().isoformat(timespec="seconds")))
                con.commit()
                logger.info("已更新 /quark 存储（开启转码直链）")
            con.close()
            return
        now = datetime.datetime.now().isoformat(timespec="seconds")
        cur.execute(
            "INSERT INTO x_storages (mount_path, [order], driver, cache_expiration, status, addition,"
            " remark, modified, disabled, disable_index, enable_sign, order_by, order_direction,"
            " extract_folder, web_proxy, webdav_policy, proxy_range, down_proxy_url, down_proxy_sign,"
            " custom_cache_policies)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ('/quark', 0, 'Quark', 30, 'work', addition, 'quark-auto', now, 0, None, None,
             'name', 'asc', 'front', 0, '302_redirect', None, None, 1, ''),
        )
        con.commit()
        con.close()
        logger.info("已自动挂载夸克存储 /quark")
    except Exception as e:
        logger.warning("挂载夸克存储失败: %s", e)


def _start_alist() -> bool:
    if not os.path.exists(_alist_exe()):
        logger.warning("AList 未安装: %s", _alist_exe())
        return False
    os.makedirs(cfg.ALIST_DATA_DIR, exist_ok=True)
    os.makedirs(cfg.DRPYS_LOG_DIR, exist_ok=True)
    env = dict(os.environ)
    env["ALIST_DATA_DIR"] = cfg.ALIST_DATA_DIR
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    try:
        log = open(os.path.join(cfg.DRPYS_LOG_DIR, "alist.log"), "a", encoding="utf-8")
        err = open(os.path.join(cfg.DRPYS_LOG_DIR, "alist.err.log"), "a", encoding="utf-8")
        subprocess.Popen(
            [_alist_exe(), "server"],
            cwd=cfg.ALIST_DIR, env=env, stdout=log, stderr=err,
            creationflags=flags, close_fds=True,
        )
        logger.info("AList 侧车已启动")
        return True
    except Exception as e:
        logger.warning("AList 启动失败: %s", e)
        return False


def _start_atv() -> bool:
    if not os.path.exists(cfg.ATV_JAR):
        logger.warning("alist-tvbox jar 不存在: %s", cfg.ATV_JAR)
        return False
    if not os.path.exists(_java_exe()):
        logger.warning("JRE21 不存在: %s", _java_exe())
        return False
    os.makedirs(cfg.ATV_DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(cfg.ATV_DIR, "log"), exist_ok=True)
    os.makedirs(cfg.DRPYS_LOG_DIR, exist_ok=True)
    h2 = "jdbc:h2:file:" + cfg.ATV_H2_DB.replace("\\", "/")
    logfile = os.path.join(cfg.ATV_DIR, "log", "app.log").replace("\\", "/")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    env = dict(os.environ)
    env["ALIST_URL"] = cfg.ALIST_TVBOX_ALIST_URL
    try:
        log = open(os.path.join(cfg.DRPYS_LOG_DIR, "atv.out.log"), "a", encoding="utf-8")
        err = open(os.path.join(cfg.DRPYS_LOG_DIR, "atv.err.log"), "a", encoding="utf-8")
        subprocess.Popen(
            [
                _java_exe(),
                "-Xmx" + cfg.ATV_JAVA_MEM,
                "-Dspring.config.additional-location=file:" + os.path.join(cfg.ATV_DIR, "config") + "/",
                "-Dspring.datasource.jdbc-url=" + h2,
                "-Dlogging.file.name=" + logfile,
                "-jar", cfg.ATV_JAR,
                "--server.address=127.0.0.1",
                "--server.port=4567",
            ],
            cwd=cfg.ATV_DIR, env=env, stdout=log, stderr=err,
            creationflags=flags, close_fds=True,
        )
        logger.info("alist-tvbox 侧车已启动")
        return True
    except Exception as e:
        logger.warning("alist-tvbox 启动失败: %s", e)
        return False


def ensure_started(wait_seconds: float = 60.0) -> bool:
    """确保 AList 与 alist-tvbox 均在运行（启动顺序：junction -> AList -> jar）。"""
    if not cfg.ALIST_TVBOX_ENABLED:
        return False
    _ensure_junctions()
    if not _alist_ready():
        _ensure_quark_storage()
        if not _start_alist():
            return False
        deadline = time.time() + 30
        while time.time() < deadline:
            if _alist_ready():
                break
            time.sleep(0.5)
    if not _atv_ready():
        _fix_enabled_token()
        if not _start_atv():
            return False
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if _atv_ready():
                logger.info("alist-tvbox 已就绪")
                return True
            time.sleep(1)
        logger.warning("alist-tvbox 启动超时，请查看 sidecar/logs/atv.out.log")
        return False
    return True


def stop():
    """停止 alist-tvbox 与 AList（供维护脚本使用）。"""
    for exe in ("java.exe", "alist.exe"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, timeout=15)
        except Exception:
            pass


def status() -> dict:
    return {
        "alist": _alist_ready(),
        "alist_tvbox": _atv_ready(),
        "base_url": cfg.ALIST_TVBOX_BASE_URL,
        "alist_url": cfg.ALIST_TVBOX_ALIST_URL,
    }
