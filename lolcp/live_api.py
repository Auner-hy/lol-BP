"""Riot 官方 Live Client Data API（游戏进行中）。

游戏开始后，客户端会在本机开放只读 HTTP 服务（默认 2999 端口）：
  https://127.0.0.1:2999/liveclientdata/allgamedata
这是 Riot 官方文档公开支持的接口，第三方助手（含 Overwolf 生态）均使用它。
官方文档：https://developer.riotgames.com/docs/lol  (Live Client Data API)

仅 127.0.0.1 可访问，不包含账号凭证、历史战绩或 MMR。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LIVE_BASE = "https://127.0.0.1:2999/liveclientdata"

# 游戏内 position 字段 -> 统一分路名
POSITION_MAP = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MIDDLE": "middle",
    "BOTTOM": "bottom",
    "UTILITY": "support",
    "SUPPORT": "support",
    "NONE": "",
    "": "",
}


@dataclass
class LivePlayer:
    champion_name: str          # 英文内部名，如 "MonkeyKing"
    display_name: str           # 客户端语言下的显示名（国服为中文名）
    position: str
    team: str                   # ORDER(蓝方) / CHAOS(红方)
    summoner: str
    is_self: bool = False


@dataclass
class LiveGame:
    active: bool = False
    mode: str = ""
    game_time: float = 0.0
    my_team: str = ""
    my_position: str = ""
    my_champion: str = ""
    players: List[LivePlayer] = field(default_factory=list)

    def enemies(self) -> List[LivePlayer]:
        """属性：返回敌方五名玩家。"""
        return [p for p in self.players if p.team != self.my_team]

    def enemy_in_my_lane(self) -> Optional[LivePlayer]:
        """属性：返回与我同一路的敌方玩家（对位目标）。"""
        if not self.my_position:
            return None
        for p in self.enemies():
            if p.position == self.my_position:
                return p
        return None


class LiveClient:
    def __init__(self, timeout: float = 4.0):
        """初始化 live 客户端实时数据访问对象。"""
        self.timeout = timeout
        self._s = requests.Session()
        self._s.verify = False

    def is_live(self) -> bool:
        """判断当前是否处于游戏进行中（live client 能否取到数据）。"""
        try:
            r = self._s.get(f"{LIVE_BASE}/gamestats", timeout=self.timeout)
            return r.status_code == 200
        except Exception:
            return False

    def all_game_data(self) -> Optional[LiveGame]:
        """拉取 live client 的全部实时对局数据。"""
        try:
            r = self._s.get(f"{LIVE_BASE}/allgamedata", timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except Exception:
            return None

        game = LiveGame(active=True)
        gd = data.get("gameData", {}) or {}
        game.mode = gd.get("gameMode", "")
        game.game_time = float(gd.get("gameTime", 0) or 0)

        active = (data.get("activePlayer", {}) or {})
        my_name = active.get("summonerName", "")
        my_champ_raw = ""
        # activePlayer 里没有直接给 championName，用 playerlist 对齐
        players_raw = data.get("allPlayers", []) or []

        for p in players_raw:
            champ = p.get("championName", "") or ""
            display = p.get("rawChampionName", "") or ""
            # rawChampionName 形如 game_character_displayname_Annie
            if "_" in display:
                display = display.rsplit("_", 1)[-1]
            summoner = p.get("riotId") or p.get("summonerName") or ""
            pos = POSITION_MAP.get(str(p.get("position", "")).upper(), "")
            team = p.get("team", "")
            is_self = bool(my_name) and (summoner == my_name or p.get("summonerName") == my_name)
            lp = LivePlayer(
                champion_name=champ,
                display_name=display if display else champ,
                position=pos,
                team=team,
                summoner=summoner,
                is_self=is_self,
            )
            game.players.append(lp)
            if is_self:
                game.my_team = team
                game.my_position = pos
                game.my_champion = champ

        # 极端情况下没匹配到自己（名字字段不一致），退化为 ORDER 队
        if not game.my_team and game.players:
            game.my_team = game.players[0].team
        return game
