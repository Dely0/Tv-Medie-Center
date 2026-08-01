"""Build a portable TV Media Center bundle (all on D:/, no C: usage).

Output: portable/TvMediaCenter/ + portable/TvMediaCenter-portable.zip
  - bundled Python (embeddable, with fastapi/uvicorn/requests installed into it)
  - project code + config
  - sidecar Node + drpys (optional --no-sidecar)

Usage:
  python scripts/build_portable.py [--no-sidecar] [--skip-downloads]
"""
import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "portable", "TvMediaCenter")
PY_VERSION = "3.12.8"
PY_URL = (f"https://npmmirror.com/mirrors/python/{PY_VERSION}/"
          f"python-{PY_VERSION}-embed-amd64.zip")

SKIP_DIRS = {".git", "__pycache__", "sidecar", "portable", "docs", ".claude"}
SKIP_FILES = {"media.db", "media.db-shm", "media.db-wal", "server.log",
              "source_speed_cache.json", "source_health.json",
              "parse_sources.json", "source_registry.json",
              "drpy_adult_sources.json", "douban_cache.json"}


def copy_project():
    if os.path.exists(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DIST)
    for entry in os.listdir(ROOT):
        if entry in SKIP_DIRS:
            continue
        src = os.path.join(ROOT, entry)
        dst = os.path.join(DIST, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "media.db*", "server.log",
                "source_speed_cache.json", "source_health.json",
                "parse_sources.json", "source_registry.json",
                "drpy_adult_sources.json", "douban_cache.json",
            ))
        elif entry not in SKIP_FILES:
            shutil.copy2(src, dst)
    print("[1/4] project copied")


def setup_python():
    py_dir = os.path.join(DIST, "python")
    os.makedirs(py_dir, exist_ok=True)
    zip_path = os.path.join(py_dir, "embed.zip")
    print("[2/4] downloading embedded python ...")
    urllib.request.urlretrieve(PY_URL, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(py_dir)
    os.remove(zip_path)
    # enable site-packages
    pth = os.path.join(py_dir, f"python{PY_VERSION[:4]}._pth")
    if os.path.exists(pth):
        lines = open(pth, encoding="utf-8").read().splitlines()
        out = []
        for line in lines:
            if line.strip() == "#import site":
                out.append("import site")
            elif line.strip() == "# Lib/site-packages":
                out.append("Lib/site-packages")
            else:
                out.append(line)
        open(pth, "w", encoding="utf-8").write("\n".join(out) + "\n")
    # install deps with system pip into embeddable site-packages
    site = os.path.join(py_dir, "Lib", "site-packages")
    os.makedirs(site, exist_ok=True)
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--quiet",
        "--target", site,
        "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
        "fastapi==0.111.0", "uvicorn==0.29.0", "requests",
    ])
    print("[2/4] embedded python ready")


def copy_sidecar():
    if os.path.exists(os.path.join(ROOT, "sidecar", "node")) and \
       os.path.exists(os.path.join(ROOT, "sidecar", "drpys")):
        shutil.copytree(os.path.join(ROOT, "sidecar", "node"),
                        os.path.join(DIST, "sidecar", "node"))
        shutil.copytree(os.path.join(ROOT, "sidecar", "drpys"),
                        os.path.join(DIST, "sidecar", "drpys"))
        print("[3/4] sidecar (node+drpys) copied")
    else:
        print("[3/4] WARNING: sidecar missing, run scripts/setup_drpys.ps1 first")


def write_scripts():
    with open(os.path.join(DIST, "start.bat"), "w", encoding="utf-8") as f:
        f.write('@echo off\r\n'
                'chcp 65001 >nul\r\n'
                'cd /d "%~dp0"\r\n'
                'start "" /B python\\python.exe -X utf8 main.py > data\\server.log 2>&1\r\n'
                ':wait\r\n'
                'timeout /t 2 /nobreak >nul\r\n'
                'powershell -Command "try{($wc=New-Object Net.WebClient).DownloadString(\'http://localhost:8080/\')|Out-Null;exit 0}catch{exit 1}" >nul 2>&1\r\n'
                'if errorlevel 1 goto wait\r\n'
                'start msedge.exe --start-fullscreen --new-window http://localhost:8080\r\n')
    with open(os.path.join(DIST, "start-all.bat"), "w", encoding="utf-8") as f:
        f.write('@echo off\r\n'
                'chcp 65001 >nul\r\n'
                'cd /d "%~dp0"\r\n'
                'call scripts\\start_drpys.bat\r\n'
                'start "" /B python\\python.exe -X utf8 main.py > data\\server.log 2>&1\r\n'
                ':wait\r\n'
                'timeout /t 2 /nobreak >nul\r\n'
                'powershell -Command "try{($wc=New-Object Net.WebClient).DownloadString(\'http://localhost:8080/\')|Out-Null;exit 0}catch{exit 1}" >nul 2>&1\r\n'
                'if errorlevel 1 goto wait\r\n'
                'start msedge.exe --start-fullscreen --new-window http://localhost:8080\r\n')
    print("[4/4] launcher scripts written")


def make_zip():
    zpath = os.path.join(ROOT, "portable", "TvMediaCenter-portable.zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(DIST):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, os.path.dirname(DIST)))
    print("zip:", zpath)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sidecar", action="store_true")
    ap.add_argument("--skip-downloads", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args()
    copy_project()
    if not args.skip_downloads:
        setup_python()
    if not args.no_sidecar:
        copy_sidecar()
    write_scripts()
    if not args.no_zip and not args.skip_downloads:
        make_zip()
    print("done:", DIST)


if __name__ == "__main__":
    main()
