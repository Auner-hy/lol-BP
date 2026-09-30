"""自动更新模块（auto-updater）：检测新版本、下载、替换并重启。

整体流程（三步，跨进程）：
  1. check_for_update() 查 GitHub 最新 Release，与当前版本比较；
  2. download() 把新 exe 下载到本地暂存目录，带进度回调；
  3. install_and_restart() 生成一个独立的 .bat 批处理脚本并启动它，
     然后由调用方退出主程序；批处理等待主程序关闭 → 覆盖 exe → 重新启动。

为什么需要 .bat：Windows 下一个正在运行的 .exe 文件被系统锁定，
程序无法覆盖自己，必须由"另一个进程"在它退出后完成替换。

注意：
  - 这里只查 GitHub 公共 Release，不需要任何账号 / Token；
  - 下载完成后是否安装，完全由用户点击决定，不静默替换。
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
# 未登录调用 GitHub API 的限制是每小时 60 次，启动检测一次完全够用
HTTP_TIMEOUT = 8.0
ASSET_NAME = "LOLCounterPicker.exe"


@dataclass
class UpdateInfo:
    """一次"有新版本"检测的结果。"""
    version: str            # 新版本号（如 1.4.0 或 1.4.0-beta）
    title: str              # Release 标题
    notes: str              # 更新说明（Release body，纯文本）
    download_url: str       # 新 exe 的直接下载地址
    release_url: str = ""   # GitHub Release 页面地址（手动下载兜底用）
    prerelease: bool = False  # 是否为测试版（预发布）
    size: int = 0           # 下载文件字节数（未知时为 0）
    expected_sha256: str = ""  # GitHub 资产官方 SHA256（下载完整性校验用）


# ============================ 版本号比较 ============================
def _parse_version(v: str):
    """把版本字符串解析成可比较的结构。

    支持形如 '1.4.0' 与 '1.4.0-beta'：
      返回 (主版本号元组, 是否预发布)，如
        '1.4.0'       -> ((1,4,0), False)
        '1.4.0-beta'  -> ((1,4,0), True)
    预发布（beta）在同一版本号下被视为"更旧"，符合语义化版本规则。
    """
    s = (v or "").strip().lstrip("vV")
    is_pre = False
    # 预发布标识：常见为 beta / pre / rc 等，统一按"是预发布"处理
    for sep in ("-", "_", "+"):
        if sep in s:
            core, tail = s.split(sep, 1)
            s = core
            is_pre = bool(tail.strip())
            break
    nums = []
    for part in s.split("."):
        # 只取每段里的数字部分，取不到就当 0
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (tuple(nums[:3]), is_pre)


def is_newer(remote: str, current: str) -> bool:
    """判断远程版本是否比当前版本新。

    比较规则：先比主.次.修订数字；数字完全相同时，正式版 > 测试版
    （所以 1.4.0 比 1.4.0-beta 新，但 1.5.0-beta 比 1.4.0 新）。
    """
    r_nums, r_pre = _parse_version(remote)
    c_nums, c_pre = _parse_version(current)
    if r_nums != c_nums:
        return r_nums > c_nums
    # 数字相同：远程不是预发布、而本地是预发布 → 远程更新
    return (not r_pre) and c_pre


# ============================ 检测更新 ============================
def _release_from_json(item: dict) -> UpdateInfo | None:
    """把一条 GitHub Release JSON 转成 UpdateInfo（找不到 exe 资产则返回 None）。"""
    tag = str(item.get("tag_name", "")).strip()
    # 版本号去掉开头的 v（如 v1.4.0 -> 1.4.0）
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
        # GitHub 新版 API 直接给出资产 SHA256（形如 "sha256:xxxx"），去掉前缀
        expected_sha256=str(asset.get("digest", "") or "").split(":")[-1].strip().lower(),
    )


def check_for_update(current_version: str, include_beta: bool = True,
                     timeout: float = HTTP_TIMEOUT) -> UpdateInfo | None:
    """查询 GitHub，若存在比 current_version 新的版本则返回 UpdateInfo，否则 None。

    include_beta=True 时会考虑预发布版本（测试中的用户能收到下一版 beta）。
    网络失败、被限流等情况一律返回 None（更新是附加功能，绝不影响主程序启动）。
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
            # 草稿版本任何人都看不到，直接跳过
            if item.get("draft"):
                continue
            if item.get("prerelease") and not include_beta:
                continue
            info = _release_from_json(item)
            if info and is_newer(info.version, current_version):
                candidates.append(info)
        if not candidates:
            return None
        # 可能有多个新版本，取其中版本号最大的一个
        candidates.sort(key=lambda u: _parse_version(u.version), reverse=True)
        return candidates[0]
    except Exception:
        return None


# ============================ 下载 ============================
def _sha256_of(path: Path) -> str:
    """计算文件 SHA256（分块读取，避免大文件一次性进内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(info: UpdateInfo, dest: Path,
             on_progress=None, timeout: float = 60.0) -> Path | None:
    """流式下载新 exe 到 dest。

    on_progress(已下载字节, 总字节) 用于更新进度条。
    下载完成后做校验：
      1) 文件大小与 GitHub 声明一致（已知大小时）；
      2) 文件头是 Windows PE 可执行文件（MZ）；
      3) 若 GitHub 给出了官方 SHA256，则必须完全一致。
    任一不过返回 None，绝不落地启动。
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
        # 校验一：文件存在、大于 5MB（真实构建约 14MB，可挡住截断的半成品）
        if not tmp.exists() or tmp.stat().st_size < 5 * 1024 * 1024:
            tmp.unlink(missing_ok=True)
            return None
        # 校验二：文件头是 Windows PE 可执行文件（MZ）
        with open(tmp, "rb") as f:
            head = f.read(2)
        if head != b"MZ":
            tmp.unlink(missing_ok=True)
            return None
        # 校验三：大小与官方声明一致（已知时），不一致说明在代理/网络下被截断
        if info.size and tmp.stat().st_size != info.size:
            tmp.unlink(missing_ok=True)
            return None
        # 校验四：官方 SHA256 必须一致（最严格的完整性校验）
        if info.expected_sha256 and _sha256_of(tmp) != info.expected_sha256:
            tmp.unlink(missing_ok=True)
            return None
        # 全部通过，落地为正式文件名
        if dest.exists():
            dest.unlink()
        tmp.replace(dest)
        return dest
    except Exception:
        return None


# ============================ 替换并重启 ============================
def is_frozen() -> bool:
    """是否以打包后的 exe 运行（源码运行时为 False）。"""
    return bool(getattr(sys, "frozen", False))


def _running_exe_path() -> Path | None:
    """打包成 onefile 时返回当前 exe 的真实路径；源码运行时返回 None。"""
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve()
        except Exception:
            return None
    return None


def install_and_restart(downloaded: Path) -> bool:
    """生成并启动独立批处理脚本，由它在主程序退出后替换 exe 并重启。

    成功启动批处理后返回 True（调用方随后应立即退出主程序）。
    """
    target = _running_exe_path()
    if target is None:
        return False  # 源码运行：没有 exe 可替换，应由调用方改为打开网页

    update_dir = downloaded.parent
    bat_path = update_dir / "_update.bat"
    # 批处理里用引号包住路径，兼容含空格 / 中文的目录
    src = str(downloaded)
    dst = str(target)

    # 思路：主程序启动本脚本后会立即退出。脚本循环尝试覆盖 exe，
    # 程序还没完全关闭时复制会失败，重试即可（每次等约 1 秒，最多约 30 秒）；
    # 成功后【先预热 + 等待】再启动新 exe，最后删除下载文件并自删除。
    #
    # 为什么要预热：刚覆盖写入的新 exe 会立刻触发 Windows Defender / 杀毒软件
    # 的实时扫描；若此刻马上启动，onefile 模式一边解压 DLL 到临时目录、杀软
    # 一边扫描锁定，可能在中途锁定/隔离 vcruntime140.dll 等，导致新程序报
    # "Failed to load Python DLL"。先用 type 读一遍可强制杀软同步扫描完，
    # 再 sleep 等文件系统与扫描都稳定，规避这个时序竞争（业界通用做法）。
    bat = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title LOL Counter Picker Updater\r\n"
        f'set "SRC={src}"\r\n'
        f'set "DST={dst}"\r\n'
        "set /a n=0\r\n"
        ":retry\r\n"
        'copy /y "%SRC%" "%DST%" >nul 2>nul\r\n'
        "if not errorlevel 1 goto ok\r\n"
        "set /a n+=1\r\n"
        "if %n% geq 30 goto end\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto retry\r\n"
        ":ok\r\n"
        "rem ---- 预热新 exe：强制安全软件同步扫描一遍 ----\r\n"
        'type "%DST%" >nul 2>nul\r\n'
        "rem ---- 等待约 3 秒，让文件系统与实时扫描稳定 ----\r\n"
        "ping -n 4 127.0.0.1 >nul\r\n"
        'start "" "%DST%"\r\n'
        'del /f /q "%SRC%" >nul 2>nul\r\n'
        ":end\r\n"
        # 删除暂存目录本身（此时里面的文件已清理，rmdir 只能删空目录，安全）
        'rmdir "%~dp0" 2>nul\r\n'
        # 经典批处理自删除：(goto) 结束当前批上下文，& 删除脚本自身
        '(goto) 2>nul & del /f /q "%~f0"\r\n'
    )
    with open(bat_path, "w", encoding="utf-8", newline="") as f:
        f.write(bat)

    # 以分离方式启动批处理：它独立于主程序，主程序退出后仍会继续执行
    try:
        os.system(f'start "LOL Updater" /min cmd /c "{bat_path}"')
    except Exception:
        return False
    return True
