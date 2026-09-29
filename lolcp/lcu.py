"""LCU（League Client Update）本地接口连接。

凭证获取（按优先级自动降级，全部只读、全程 127.0.0.1 回环）：
  1) lockfile：客户端安装目录下的 lockfile（含端口和随机密码）。
     官服/外服通常有效；
  2) 客户端 UX 日志：国服部分环境 lockfile 损坏（0 字节残留），但
     LeagueClientUx 启动日志里始终会记录本次会话的 --remoting-auth-token
     和 --app-port（普通文本文件，用户权限可读）。提取后实测验证，
     有效即用。该方法不依赖 lockfile、不依赖进程信息（TenProtect 无影响）。
  3) 以上端口还会与 netstat 中 LeagueClient.exe 实际监听端口交叉验证。

安全说明：
- 只发送 GET 只读请求，不修改客户端任何状态；
- 不读取游戏进程内存、不注入、不模拟键鼠；
- 数据仅在本机 127.0.0.1 回环，不经过外部服务器。
"""
from __future__ import annotations

import base64
import glob
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .config import DEFAULT_CLIENT_GLOBS

LOCKFILE_NAME = "lockfile"

# 客户端 UX 启动日志中记录的本次会话凭证：
#   --remoting-auth-token=<22字符token> ... --app-port=<端口>
# （两者先后顺序不固定，分别匹配；riotclient- 前缀的 RCS 参数不会被误匹配）
_UXTOKEN_RE = re.compile(
    r"--remoting-auth-token=([A-Za-z0-9_\-]{12,40})[^\n]{0,120}?"
    r"--app-port=(\d{2,5})")
_UXTOKEN_RE2 = re.compile(
    r"--app-port=(\d{2,5})[^\n]{0,120}?"
    r"--remoting-auth-token=([A-Za-z0-9_\-]{12,40})")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class ChampSelectPlayer:
    cell_id: int
    champion_id: int          # 0 = 未选（已锁定的英雄）
    summoner_id: int
    position: str             # top/jungle/middle/bottom/utility/""
    is_self: bool = False
    pick_intent_id: int = 0   # 预选(选择意向)英雄ID，未锁定时也有值；0=无预选


@dataclass
class ChampSelectState:
    is_active: bool = False
    phase: str = ""                       # 如 CHAMP_SELECT / FINALIZATION
    my_position: str = ""
    my_champion_id: int = 0
    my_pick_intent_id: int = 0   # 我方预选英雄ID（锁定前可用），0=无
    self_cell_id: int = -1
    allies: List[ChampSelectPlayer] = field(default_factory=list)
    enemies: List[ChampSelectPlayer] = field(default_factory=list)
    banned_champion_ids: List[int] = field(default_factory=list)

    def locked_enemies(self) -> List[ChampSelectPlayer]:
        """属性：返回敌方队伍中已锁定英雄的玩家列表。"""
        return [p for p in self.enemies if p.champion_id and p.champion_id > 0]

    def enemy_in_my_lane(self) -> Optional[ChampSelectPlayer]:
        """返回与我同位置的敌方玩家（需要位置已分配，排位/征召模式可用）。"""
        if not self.my_position:
            return None
        for p in self.enemies:
            if p.position and p.position == self.my_position and p.champion_id:
                return p
        return None


class LCUClient:
    """英雄联盟客户端本地接口客户端。"""

    def __init__(self, client_path: str = "", timeout: float = 5.0):
        """初始化选将会话，并立即拉取双方队伍数据。"""
        self.client_path = client_path
        self.timeout = timeout
        self.port: Optional[int] = None
        self.password: Optional[str] = None
        self.protocol = "https"
        self.cred_source: str = ""        # lockfile / ux-log（诊断用）
        self._session: Optional[requests.Session] = None
        self._last_discovery_ts: float = 0.0

    # ---------- 凭证来源 ----------
    @staticmethod
    def _valid_lockfile(path: Path) -> bool:
        """lockfile 必须存在、非空且格式合法（LeagueClient:<pid>:<port>:<pwd>:<proto>）。
        客户端退出后残留的空 lockfile 一律跳过。"""
        try:
            if not path.is_file() or path.stat().st_size < 10:
                return False
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            parts = text.split(":")
            return len(parts) >= 5 and parts[2].isdigit() and int(parts[2]) > 0
        except OSError:
            return False

    def _iter_client_dirs(self):
        """惰性枚举可能的 LeagueClient 安装目录（去重、存在才产出）。

        惰性设计：快路径（手动路径 → 内置路径表）命中时，不会执行昂贵的
        PowerShell 进程查询和盘符扫描。"""
        seen = set()

        def add(p):
            """把目录加入枚举结果（去重，且确认其真实存在才返回）。"""
            if not p:
                return None
            pth = Path(p)
            key = str(pth).lower()
            if key in seen:
                return None
            seen.add(key)
            try:
                if pth.is_dir():
                    return pth
            except OSError:
                return None
            return None

        # 1) 用户手动指定
        if self.client_path:
            d = add(self.client_path)
            if d:
                yield d
        # 2) 内置常见路径表
        for g in DEFAULT_CLIENT_GLOBS:
            d = add(g)
            if d:
                yield d
        # 3) 进程定位（Windows；部分机器被反作弊拦截会返回空，忽略）
        d = add(self._find_client_dir_via_process())
        if d:
            yield d
        # 4) 盘符浅扫描（WeGameApps/* 通配可同时覆盖中文名和国服特有的乱码目录）
        if sys.platform.startswith("win"):
            import string
            patterns = (
                "WeGameApps/*/LeagueClient",
                "WeGameApps/英雄联盟/LeagueClient",
                "WeGameApps/LOL/LeagueClient",
                "WeGame*/英雄联盟/LeagueClient",
                "WeGame*/LOL/LeagueClient",
                "英雄联盟/LeagueClient",
                "LOL/LeagueClient",
                "League of Legends/LeagueClient",
                "*/英雄联盟/LeagueClient",
                "*/LOL/LeagueClient",
                "*/League of Legends/LeagueClient",
                "*/*/英雄联盟/LeagueClient",
                "*/*/LOL/LeagueClient",
                "Program Files/Riot Games/League of Legends/LeagueClient",
                "Program Files (x86)/Riot Games/League of Legends/LeagueClient",
            )
            for letter in string.ascii_uppercase:
                base = Path(f"{letter}:/")
                try:
                    if not base.exists():
                        continue
                except OSError:
                    continue
                for pat in patterns:
                    try:
                        for cand in base.glob(pat):
                            d = add(cand)
                            if d:
                                yield d
                    except (OSError, PermissionError):
                        continue

    @staticmethod
    def _find_client_dir_via_process() -> Optional[Path]:
        """通过 League 进程命令行参数定位客户端安装目录（--install-directory）。"""
        if not sys.platform.startswith("win"):
            return None
        # 优先 Get-Process（不依赖 WMI/CIM 服务；部分机器 WMI 损坏会报"无效类"）
        ps_cmds = [
            "(Get-Process LeagueClient -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 -ExpandProperty Path)",
            # 兜底：WMI 查询（老系统）
            "Get-CimInstance Win32Process -Filter \"Name='LeagueClient.exe'\" "
            "| Select-Object -First 1 -ExpandProperty ExecutablePath",
        ]
        for ps in ps_cmds:
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True, text=True, timeout=8,
                    creationflags=_CREATE_NO_WINDOW,
                )
                path = (out.stdout or "").strip()
                if path.lower().endswith("leagueclient.exe"):
                    return Path(path).parent
            except Exception:
                continue
        return None

    # ----- 凭证来源 B：UX 启动日志（国服 lockfile 损坏时的主通道）-----
    @staticmethod
    def _creds_from_ux_log(client_dir: Path):
        """从某个安装目录最新的 LeagueClientUx 日志提取本次会话凭证。
        返回 (token, port) 或 None。"""
        try:
            logs = [p for p in client_dir.glob("*LeagueClientUx.log")
                    if p.is_file()]
            logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        for lp in logs[:2]:
            try:
                txt = lp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            pairs = [(m.group(1), int(m.group(2)))
                     for m in _UXTOKEN_RE.finditer(txt)]
            pairs += [(m.group(2), int(m.group(1)))
                      for m in _UXTOKEN_RE2.finditer(txt)]
            if pairs:
                return pairs[-1]  # 文件内最后一条 = 最近一次启动
        return None

    @staticmethod
    def _lcu_listening_ports() -> List[int]:
        """netstat + tasklist：当前 LeagueClient.exe 监听的 127.0.0.1 端口。
        不需要管理员权限，反作弊不影响 netstat。"""
        def _run(cmd):
            """执行一条命令并返回其标准输出文本（失败时返回空字符串）。"""
            try:
                out = subprocess.run(
                    cmd, capture_output=True, timeout=10,
                    creationflags=_CREATE_NO_WINDOW)
                return (out.stdout or b"").decode("gbk", errors="ignore")
            except Exception:
                return ""

        ns = _run(["netstat", "-ano", "-p", "tcp"])
        pid_ports = {}
        for line in ns.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" \
                    and parts[-2] == "LISTENING" and parts[-1].isdigit():
                local = parts[1]
                if local.startswith("127.0.0.1:"):
                    port_s = local.rsplit(":", 1)[1]
                    if port_s.isdigit():
                        pid_ports.setdefault(int(parts[-1]), set()).add(
                            int(port_s))
        tl = _run(["tasklist", "/fo", "csv", "/nh"])
        ports = set()
        for line in tl.splitlines():
            cols = line.split('","')
            if len(cols) >= 2 and \
                    cols[0].strip('"').lower() == "leagueclient.exe":
                pid_s = cols[1].strip('"')
                if pid_s.isdigit():
                    ports.update(pid_ports.get(int(pid_s), set()))
        return sorted(ports)

    # ---------- 连接 ----------
    def _try_cred(self, port: int, password: str,
                  proto: str = "https", timeout: float = 3.0) -> bool:
        """用一组凭证实测 LCU 接口，200 即保留会话。"""
        try:
            token = base64.b64encode(f"riot:{password}".encode()).decode()
            s = requests.Session()
            s.verify = False
            s.headers.update({
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "User-Agent": "lol-counter-picker/1.0",
            })
            r = s.get(
                f"{proto}://127.0.0.1:{port}/lol-summoner/v1/current-summoner",
                timeout=timeout)
            if r.status_code == 200:
                if self._session:
                    try:
                        self._session.close()
                    except Exception:
                        pass
                self._session = s
                return True
            s.close()
        except Exception:
            return False
        return False

    def connect(self) -> bool:
        """发现凭证并建立会话。返回客户端是否在线。

        按安装目录惰性枚举：每个目录先试 lockfile，再试该目录最新 UX 日志
        里的令牌（日志端口优先，失败后用 netstat 实际监听端口交叉验证）。
        快路径目录命中即返回，不触发进程查询/全盘扫描。每组凭证均实测
        通过才采用。"""
        now = time.time()
        if self._session is None and self._last_discovery_ts \
                and now - self._last_discovery_ts < 10:
            return False  # 客户端离线时限速重扫，避免每 2 秒读日志/扫描
        self._last_discovery_ts = now

        listen_ports: Optional[List[int]] = None  # 懒加载，只跑一次

        for d in self._iter_client_dirs():
            # 来源 1：lockfile（官服/外服）
            lf = d / LOCKFILE_NAME
            if self._valid_lockfile(lf):
                try:
                    text = lf.read_text(
                        encoding="utf-8", errors="ignore").strip()
                    parts = text.split(":")
                    if len(parts) >= 5 and parts[2].isdigit():
                        proto = parts[4] if parts[4] in ("https", "http") \
                            else "https"
                        if self._try_cred(int(parts[2]), parts[3], proto):
                            self.port = int(parts[2])
                            self.password = parts[3]
                            self.protocol = proto
                            self.cred_source = "lockfile"
                            return True
                except OSError:
                    pass

            # 来源 2：UX 启动日志（国服 lockfile 损坏时的主通道）
            creds = self._creds_from_ux_log(d)
            if creds:
                token, log_port = creds
                if self._try_cred(log_port, token, "https"):
                    self.port, self.password = log_port, token
                    self.protocol, self.cred_source = "https", "ux-log"
                    return True
                # 日志端口失效：用 netstat 实际监听端口交叉验证
                if listen_ports is None:
                    listen_ports = self._lcu_listening_ports()
                for p in listen_ports:
                    if p == log_port:
                        continue
                    if self._try_cred(p, token, "https"):
                        self.port, self.password = p, token
                        self.protocol, self.cred_source = "https", "ux-log"
                        return True
                if not listen_ports:
                    # 最新日志令牌被拒 + 系统中无 LeagueClient 监听端口
                    # = 客户端未运行，无需再扫其他目录/进程
                    self._session = None
                    self.cred_source = ""
                    return False

        self._session = None
        self.cred_source = ""
        return False

    def is_connected(self) -> bool:
        """判断当前 LCU 连接是否仍然有效。"""
        if not self._session:
            return False
        try:
            self._get("/lol-summoner/v1/current-summoner")
            return True
        except Exception:
            return False

    def ensure_connected(self) -> bool:
        """确保已连接 LCU；尚未连接或连接失效时自动重连。"""
        if self.is_connected():
            return True
        return self.connect()

    def _get(self, path: str, **kwargs):
        """向 LCU 发送 HTTPS GET 请求（自动携带本地鉴权信息）。"""
        if not self._session:
            raise RuntimeError("LCU 未连接")
        url = f"{self.protocol}://127.0.0.1:{self.port}{path}"
        r = self._session.get(url, timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r.json()

    # ---------- 业务数据 ----------
    def current_summoner(self) -> Optional[dict]:
        """获取当前登录召唤师的基础信息。"""
        try:
            return self._get("/lol-summoner/v1/current-summoner")
        except Exception:
            return None

    def champ_select(self) -> Optional[ChampSelectState]:
        """读取当前选将（BP）会话；不在选将阶段返回 None。"""
        try:
            data = self._get("/lol-champ-select/v1/session")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 500):
                return None  # 不在选将阶段
            return None
        except Exception:
            return None
        if not data:
            return None

        state = ChampSelectState(is_active=True)
        state.phase = str(data.get("timer", {}).get("phase", ""))
        state.self_cell_id = int(data.get("localPlayerCellId", -1))

        bans: List[int] = []
        for action_group in data.get("actions", []) or []:
            for act in action_group or []:
                if act.get("type") == "ban" and act.get("completed"):
                    cid = act.get("championId")
                    if cid and int(cid) > 0:
                        bans.append(int(cid))
        state.banned_champion_ids = bans

        def parse_team(team_key: str) -> List[ChampSelectPlayer]:
            """从选将数据中解析指定队伍（己方/敌方）的玩家列表。"""
            players = []
            for p in data.get(team_key, []) or []:
                cid = int(p.get("championId") or 0)
                cell = int(p.get("cellId", -1))
                # championPickIntent = 预选(选择意向)英雄，锁定前也存在
                intent = int(p.get("championPickIntent") or 0)
                players.append(ChampSelectPlayer(
                    cell_id=cell,
                    champion_id=cid,
                    summoner_id=int(p.get("summonerId") or 0),
                    position=str(p.get("assignedPosition") or "").lower(),
                    is_self=(cell == state.self_cell_id),
                    pick_intent_id=intent,
                ))
            return players

        state.allies = parse_team("myTeam")
        state.enemies = parse_team("theirTeam")

        me = next((p for p in state.allies if p.is_self), None)
        if me:
            state.my_position = me.position
            state.my_champion_id = me.champion_id
            state.my_pick_intent_id = me.pick_intent_id
        return state
