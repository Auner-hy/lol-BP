"""自动更新模块（auto-updater）：检测新版本、下载、解压替换并重启。

打包模式：onedir（文件夹模式）。程序不再是单个 exe，而是一个文件夹：
    LOLCounterPicker/
      LOLCounterPicker.exe      <- 用户双击的启动器
      _internal/                <- 运行库与所有依赖（DLL、Python、资源）

为什么放弃 onefile（单文件）：单文件每次启动都把运行库解压到临时目录，
而火绒等安全软件会在解压瞬间拦截/锁定 DLL，间歇报
"Failed to load Python DLL"。onedir 文件常驻程序目录、启动时直接读取，
不再解压，从根上消除该问题，启动也更快。

更新整体流程（跨进程）：
  1. check_for_update() 查 GitHub 最新 Release；
  2. download() 下载新版本的 zip 到暂存目录，校验大小 / SHA256；
  3. install_and_restart() 生成独立 .bat 并启动，随后主程序退出；
     bat：等待主程序关闭 → 解压 zip 到【新的版本目录】→ 启动新 exe
     → 新程序用 after 自检确认存活后，清理旧目录与暂存文件。

为什么解压到"新目录"而不是原地覆盖：onedir 有上千个文件，正在运行的
文件被锁定、且逐个覆盖极易被安全软件打断；整体解压到新目录是原子、
干净、可回滚的做法。

只查 GitHub 公共 Release，无需账号 / Token；是否安装由用户点击决定。
"""
from __future__ import annotations

import os
import sys
import time
import hashlib
from dataclasses import dataclass
from pathlib import Path

import requests

# GitHub 仓库信息
REPO_OWNER = "Auner-hy"
REPO_NAME = "lol-BP"
API_ROOT = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
HTTP_TIMEOUT = 8.0
# onedir 模式的发布资产是一个 zip（内含 LOLCounterPicker 文件夹）
ASSET_NAME = "LOLCounterPicker.zip"
APP_DIR_NAME = "LOLCounterPicker"
EXE_NAME = "LOLCounterPicker.exe"


@dataclass
class UpdateInfo:
    """一次"有新版本"检测的结果。"""
    version: str            # 新版本号（如 1.5.0-beta）
    title: str              # Release 标题
    notes: str              # 更新说明（Release body，纯文本）
    download_url: str       # 新 zip 的直接下载地址
    release_url: str = ""   # GitHub Release 页面地址（手动下载兜底）
    prerelease: bool = False  # 是否为测试版（预发布）
    size: int = 0           # 下载文件字节数（未知时为 0）
    expected_sha256: str = ""  # GitHub 资产官方 SHA256


# ============================ 版本号比较 ============================
def _parse_version(v: str):
    """解析版本字符串为 ((主,次,修订), 是否预发布)。

    '1.5.0'       -> ((1,5,0), False)
    '1.5.0-beta'  -> ((1,5,0), True)
    预发布在同版本号下视为更旧（语义化版本规则）。
    """
    s = (v or "").strip().lstrip("vV")
    is_pre = False
    for sep in ("-", "_", "+"):
        if sep in s:
            core, tail = s.split(sep, 1)
            s = core
            is_pre = bool(tail.strip())
            break
    nums = []
    for part in s.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (tuple(nums[:3]), is_pre)


def is_newer(remote: str, current: str) -> bool:
    """远程版本是否比当前版本新。

    先比主.次.修订；完全相同时正式版 > 测试版
    （1.5.0 比 1.5.0-beta 新，但 1.5.0-beta 比 1.4.4 新）。
    """
    r_nums, r_pre = _parse_version(remote)
    c_nums, c_pre = _parse_version(current)
    if r_nums != c_nums:
        return r_nums > c_nums
    return (not r_pre) and c_pre


# ============================ 检测更新 ============================
def _release_from_json(item: dict) -> UpdateInfo | None:
    """把 GitHub Release JSON 转成 UpdateInfo（找不到 zip 资产则 None）。"""
    tag = str(item.get("tag_name", "")).strip()
    version = tag[1:] if tag[:1] in ("v", "V") else tag
    asset = None
    for a in item.get("assets", []) or []:
        if str(a.get("name", "")).lower() == ASSET_NAME.lower():
            asset = a
            break
    if not asset:
        return None
    return UpdateInfo(
        version=version,
        title=str(item.get("name", "") or tag),
        notes=str(item.get("body", "") or "").strip(),
        download_url=str(asset.get("browser_download_url", "")),
        release_url=str(item.get("html_url", "") or ""),
        prerelease=bool(item.get("prerelease", False)),
        size=int(asset.get("size", 0) or 0),
        expected_sha256=str(asset.get("digest", "") or "").split(":")[-1].strip().lower(),
    )


def check_for_update(current_version: str, include_beta: bool = True,
                     timeout: float = HTTP_TIMEOUT) -> UpdateInfo | None:
    """查 GitHub，存在比 current_version 新的版本则返回 UpdateInfo，否则 None。

    网络失败 / 限流一律返回 None（更新是附加功能，绝不影响主程序启动）。
    """
    try:
        r = requests.get(
            f"{API_ROOT}/releases",
            params={"per_page": 10},
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "lol-counter-picker-updater"},
            timeout=timeout)
        if r.status_code != 200:
            return None
        items = r.json()
        if not isinstance(items, list):
            return None
        candidates = []
        for item in items:
            if item.get("draft"):
                continue
            if item.get("prerelease") and not include_beta:
                continue
            info = _release_from_json(item)
            if info and is_newer(info.version, current_version):
                candidates.append(info)
        if not candidates:
            return None
        candidates.sort(key=lambda u: _parse_version(u.version), reverse=True)
        return candidates[0]
    except Exception:
        return None


# ============================ 下载 ============================
def _sha256_of(path: Path) -> str:
    """分块计算文件 SHA256，避免大文件一次性进内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(info: UpdateInfo, dest: Path,
             on_progress=None, timeout: float = 180.0) -> Path | None:
    """流式下载新 zip 到 dest，下载后严格校验。

    校验：①文件大小与 GitHub 一致；②文件头是 zip（PK）；
    ③官方 SHA256 一致。任一不过返回 None，绝不落地。
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with requests.get(info.download_url, stream=True, timeout=timeout) as r:
            if r.status_code != 200:
                return None
            total = int(r.headers.get("Content-Length", 0) or info.size or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
        if not tmp.exists() or tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            return None
        # 校验：zip 文件头为 PK(0x50 0x4B)
        with open(tmp, "rb") as f:
            head = f.read(2)
        if head != b"PK":
            tmp.unlink(missing_ok=True)
            return None
        # 校验：大小与官方声明一致
        if info.size and tmp.stat().st_size != info.size:
            tmp.unlink(missing_ok=True)
            return None
        # 校验：官方 SHA256 一致
        if info.expected_sha256:
            digest = _sha256_of(tmp)
            if digest != info.expected_sha256:
                tmp.unlink(missing_ok=True)
                return None
        if dest.exists():
            dest.unlink()
        tmp.replace(dest)
        return dest
    except Exception:
        return None


# ============================ 路径与替换重启 ============================
def is_frozen() -> bool:
    """是否以打包后的程序运行（源码运行为 False）。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path | None:
    """打包运行时返回程序根目录（exe 所在目录，onedir 下即 LOLCounterPicker/）。

    源码运行时返回 None。
    """
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return None
    return None


def install_and_restart(
    downloaded_zip: Path,
    new_version: str,
    _test_root: "str | Path | None" = None,
    _test_update_dir: "str | Path | None" = None,
    _test_stamp: "str | Path | None" = None,
) -> bool:
    """生成并启动独立批处理，由它在主程序退出后解压到新目录并重启。

    成功启动 bat 后返回 True（调用方随后应立即退出主程序）。

    目录布局（以装在 .../App/LOLCounterPicker 为例）：
      .../App/                         <- 安装基目录（所有版本的父目录）
        LOLCounterPicker/              <- 当前版本（旧，运行中，启动后被清理）
        LOLCounterPicker_v1.5.0/       <- 新版本解压目标
      <temp>/_lol_update/
        LOLCounterPicker.zip           <- 已下载
        _update.bat                    <- 本脚本
        stamp.txt                      <- 新版本写的存活标记
    """
    # 测试用覆盖（默认 None，正式运行不受影响）。需放在 app_root() 判断之前：
    # 源码运行时 app_root() 为 None，测试靠它注入一个“伪安装根目录”。
    if _test_root is not None:
        root = Path(_test_root)
    else:
        root = app_root()
        if root is None:
            return False  # 源码运行：无 exe，应由调用方改为打开网页
    downloaded_zip = Path(downloaded_zip)
    if _test_update_dir is not None:
        downloaded_zip = Path(_test_update_dir) / downloaded_zip.name

    install_base = root.parent
    # 新版本目录：版本号里的非法字符替换掉，得到稳定目录名
    safe_ver = "".join(ch if ch.isalnum() or ch in "._-" else "_"
                       for ch in new_version)
    new_dir = install_base / f"{APP_DIR_NAME}_{safe_ver}"
    update_dir = downloaded_zip.parent
    bat_path = update_dir / "_update.bat"
    stamp = Path(_test_stamp) if _test_stamp is not None else update_dir / "stamp.txt"

    zipf = str(downloaded_zip)
    base = str(install_base)
    ndir = str(new_dir)
    old = str(root)
    stampf = str(stamp)

    # PowerShell 负责解压（Expand-Archive，系统自带、无需额外依赖）。
    # 先清掉可能残留的半成品新目录，再整体解压。
    # 解压后 zip 内顶层即 LOLCounterPicker/，用 robocopy 搬到新版本目录，
    # 使目录名带上版本号；随后启动新 exe。
    #
    # 旧目录不立即删：新程序启动成功后会写 stamp.txt；bat 看到 stamp
    # 才删除旧目录，避免"新版本起不来、旧版本也没了"的最坏情况。
    ps_extract = (
        "Expand-Archive -LiteralPath $env:LZ -DestinationPath "
        "$env:LX -Force; "
        "$sub = Join-Path $env:LX 'LOLCounterPicker'; "
        "if (Test-Path $sub) { "
        "Remove-Item -LiteralPath $env:ND -Recurse -Force -ErrorAction SilentlyContinue; "
        "Move-Item -LiteralPath $sub -Destination $env:ND -Force }"
    )

    # PowerShell 那一行用普通字符串拼接 ps_extract 原文（含花括号），
    # 不走 f-string，避免花括号被当成占位符。
    bat = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title LOL Counter Picker Updater\r\n"
        f'set "LZ={zipf}"\r\n'
        f'set "LX={update_dir}\\extract"\r\n'
        f'set "ND={ndir}"\r\n'
        f'set "OLD={old}"\r\n'
        f'set "BASE={base}"\r\n'
        f'set "STAMP={stampf}"\r\n'
        "set /a n=0\r\n"
        # 等待旧程序退出（最多约 30 秒）：旧目录能被重命名，说明里面已无
        # 被进程锁定的文件；探测成功后立刻改回原名，再去解压。
        'set "OLDPROBE=%BASE%\\__oldprobe__"\r\n'
        'if exist "%OLDPROBE%" rmdir /s /q "%OLDPROBE%" >nul 2>nul\r\n'
        ":waitold\r\n"
        'ren "%OLD%" __oldprobe__ >nul 2>nul\r\n'
        "if not errorlevel 1 (\r\n"
        f'  ren "%OLDPROBE%" "{APP_DIR_NAME}" >nul 2>nul\r\n'
        "  goto extract\r\n"
        ")\r\n"
        "set /a n+=1\r\n"
        "if %n% geq 30 goto end\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto waitold\r\n"
        ":extract\r\n"
        'if exist "%STAMP%" del /f /q "%STAMP%" >nul 2>nul\r\n'
        'powershell -NoProfile -ExecutionPolicy Bypass -Command "'
        + ps_extract
        + '"\r\n'
        f'if not exist "%ND%\\{EXE_NAME}" goto end\r\n'
        # 给新进程注入存活标记路径，新程序自检存活后会写该文件
        'set "LOL_UPDATE_STAMP=%STAMP%"\r\n'
        f'start "" "%ND%\\{EXE_NAME}"\r\n'
        # 等待新版本自写存活标记（约 20 秒）；看到标记才清理旧目录
        "set /a w=0\r\n"
        ":waitstamp\r\n"
        'if exist "%STAMP%" goto cleanup\r\n'
        "set /a w+=1\r\n"
        "if %w% geq 20 goto keepold\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto waitstamp\r\n"
        ":cleanup\r\n"
        'rmdir /s /q "%OLD%" >nul 2>nul\r\n'
        ":keepold\r\n"
        'rmdir /s /q "%LX%" >nul 2>nul\r\n'
        'del /f /q "%LZ%" >nul 2>nul\r\n'
        'del /f /q "%STAMP%" >nul 2>nul\r\n'
        ":end\r\n"
        'rmdir "%~dp0" 2>nul\r\n'
        '(goto) 2>nul & del /f /q "%~f0"\r\n'
    )

    with open(bat_path, "w", encoding="utf-8", newline="") as f:
        f.write(bat)

    try:
        os.system(f'start "LOL Updater" /min cmd /c "{bat_path}"')
    except Exception:
        return False
    return True


def write_alive_stamp() -> None:
    """新版本启动并确认存活后调用：写入存活标记，通知更新脚本可清理旧版本。

    标记位置由环境变量 LOL_UPDATE_STAMP 提供（更新 bat 启动新程序时注入）。
    非更新启动时该变量为空，什么都不做。
    """
    stamp = os.environ.get("LOL_UPDATE_STAMP", "")
    if not stamp:
        return
    try:
        Path(stamp).write_text(f"alive {time.time()}", encoding="utf-8")
    except Exception:
        pass
