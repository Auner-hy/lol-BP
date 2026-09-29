"""状态引擎：轮询 LCU（选将阶段）与 Live Client API（游戏内），输出当前对位快照。

阶段流转：
  no_client   -> 未检测到英雄联盟客户端
  lobby       -> 客户端在线，不在选将/游戏中
  champ_select-> BP/选将阶段（LCU）
  in_game     -> 游戏进行中（Live Client Data API）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .champions import ChampionDB
from .config import Config
from .lcu import LCUClient
from .live_api import LiveClient
from .matchup import Recommender, Counter

LANE_CN = {"top": "上单", "jungle": "打野", "middle": "中单",
           "bottom": "下路", "support": "辅助", "": "未定"}

# 各数据源分路叫法不同：LCU 选将用 utility、个别接口用 mid/bot/jg 等
_LANE_ALIAS = {"utility": "support", "sup": "support", "mid": "middle",
               "jg": "jungle", "jungler": "jungle", "adc": "bottom",
               "bot": "bottom"}


def norm_lane(lane: str) -> str:
    """把任意来源的分路叫法归一到内部标准 key。"""
    if not lane:
        return ""
    return _LANE_ALIAS.get(lane.lower(), lane.lower())


@dataclass
class EnemyChamp:
    champion_id: int
    lane: str = ""           # top/jungle/...
    lane_cn: str = ""
    name: str = ""


@dataclass
class Snapshot:
    phase: str = "no_client"          # no_client / lobby / champ_select / in_game
    phase_cn: str = "未检测到客户端"
    my_lane: str = ""
    my_champ_id: int = 0
    my_pick_intent_id: int = 0        # 我方预选英雄ID（未锁定时也有），0=无
    allies: List[EnemyChamp] = field(default_factory=list)     # 我方全部英雄（含自己）
    enemies: List[EnemyChamp] = field(default_factory=list)   # 敌方全部英雄
    auto_enemy_id: int = 0            # 系统判定的对位英雄（同路敌人）
    bans: List[int] = field(default_factory=list)
    mode: str = ""
    updated_at: float = 0.0


class Engine:
    def __init__(self, config: Config):
        """初始化引擎及其数据源（LCU、live 客户端、英雄库与推荐器）。"""
        self.cfg = config
        self.db = ChampionDB(lang=config.language, timeout=config.http_timeout)
        self.lcu = LCUClient(client_path=config.client_path, timeout=5.0)
        self.live = LiveClient(timeout=4.0)
        self.recommender = Recommender(
            self.db, providers=config.data_providers,
            timeout=config.http_timeout, top_n=config.top_n, tier=config.tier)
        self.manual_enemy_id: int = 0   # 用户在界面手动点选的敌方英雄

    # ---------- 本地状态轮询（无外网） ----------
    def poll(self) -> Snapshot:
        """轮询当前状态并生成统一快照（选将阶段 / 游戏内 / 未开始）。"""
        snap = Snapshot(updated_at=time.time())
        self.db.load()

        # 1) 游戏进行中：官方 Live Client Data API 优先
        game = None
        try:
            if self.live.is_live():
                game = self.live.all_game_data()
        except Exception:
            game = None
        if game and game.active:
            return self._snapshot_from_game(snap, game)

        # 2) 选将阶段：LCU
        try:
            if not self.lcu.ensure_connected():
                snap.phase, snap.phase_cn = "no_client", "未检测到英雄联盟客户端"
                return snap
            cs = self.lcu.champ_select()
        except Exception:
            snap.phase, snap.phase_cn = "lobby", "客户端在线（等待进入对局）"
            return snap

        if cs and cs.is_active:
            return self._snapshot_from_champ_select(snap, cs)

        snap.phase, snap.phase_cn = "lobby", "客户端在线（等待进入对局）"
        return snap

    def _snapshot_from_champ_select(self, snap: Snapshot, cs) -> Snapshot:
        """选将阶段：从 LCU 数据填充快照（双方英雄、分路、禁用与阶段）。"""
        snap.phase = "champ_select"
        snap.phase_cn = "选将阶段（BP）"
        snap.my_lane = norm_lane(cs.my_position)
        snap.my_champ_id = cs.my_champion_id
        snap.my_pick_intent_id = cs.my_pick_intent_id
        snap.bans = list(cs.banned_champion_ids)
        for p in cs.allies:
            if not p.champion_id:
                continue
            snap.allies.append(EnemyChamp(
                champion_id=p.champion_id,
                lane=norm_lane(p.position),
                lane_cn=LANE_CN.get(norm_lane(p.position), ""),
                name=self.db.name_of(p.champion_id),
            ))
        for p in cs.locked_enemies():
            snap.enemies.append(EnemyChamp(
                champion_id=p.champion_id,
                lane=norm_lane(p.position),
                lane_cn=LANE_CN.get(norm_lane(p.position), ""),
                name=self.db.name_of(p.champion_id),
            ))
        lane_enemy = cs.enemy_in_my_lane()
        if lane_enemy:
            snap.auto_enemy_id = lane_enemy.champion_id
        elif snap.enemies:
            # 盲选/位置未分配：默认取第一个锁定的敌人
            snap.auto_enemy_id = snap.enemies[0].champion_id
        return snap

    def _snapshot_from_game(self, snap: Snapshot, game) -> Snapshot:
        """游戏内：从 live 客户端实时数据填充快照。"""
        snap.phase = "in_game"
        snap.phase_cn = "游戏进行中"
        snap.mode = game.mode
        snap.my_lane = norm_lane(game.my_position)
        for p in game.players:
            if p.team != game.my_team:
                continue
            cid = self.db.id_by_en(p.champion_name) or self.db.id_by_en(p.display_name)
            if not cid:
                continue
            snap.allies.append(EnemyChamp(
                champion_id=cid,
                lane=norm_lane(p.position),
                lane_cn=LANE_CN.get(norm_lane(p.position), ""),
                name=self.db.name_of(cid),
            ))
        for p in game.enemies():
            cid = self.db.id_by_en(p.champion_name) or self.db.id_by_en(p.display_name)
            if not cid:
                continue
            snap.enemies.append(EnemyChamp(
                champion_id=cid,
                lane=norm_lane(p.position),
                lane_cn=LANE_CN.get(norm_lane(p.position), ""),
                name=self.db.name_of(cid),
            ))
        lane_enemy = game.enemy_in_my_lane()
        if lane_enemy:
            cid = self.db.id_by_en(lane_enemy.champion_name) or self.db.id_by_en(lane_enemy.display_name)
            if cid:
                snap.auto_enemy_id = cid
        elif snap.enemies:
            snap.auto_enemy_id = snap.enemies[0].champion_id
        if game.my_champion:
            cid = self.db.id_by_en(game.my_champion)
            snap.my_champ_id = cid or 0
        return snap

    # ---------- 推荐（可能走外网，在后台线程调用） ----------
    def lane_matchups(self, snap: Snapshot) -> List[Tuple[str, EnemyChamp, EnemyChamp]]:
        """按分路把双方英雄配成 5 对。返回 [(lane, ally, enemy), ...]，
        缺人的一侧为 None。仅用快照数据，不发网络请求。"""
        # 数据进入快照时已归一，这里再兜一层防止异常来源
        a_by, e_by = {}, {}
        for a in snap.allies:
            if a.lane:
                a_by.setdefault(norm_lane(a.lane), a)
        for e in snap.enemies:
            if e.lane:
                e_by.setdefault(norm_lane(e.lane), e)
        out = []
        for ln in ("top", "jungle", "middle", "bottom", "support"):
            out.append((ln, a_by.get(ln), e_by.get(ln)))
        return out

    def target_enemy(self, snap: Snapshot) -> int:
        """确定当前对位目标（手动选择优先，否则取与我同路的敌方）。"""
        return self.manual_enemy_id or snap.auto_enemy_id

    def recommend(self, enemy_id: int, lane: str) -> Tuple[List[Counter], str]:
        """获取对指定敌方英雄、指定分路的克制推荐。"""
        if not enemy_id:
            return [], ""
        # 游戏内/选将同路对位时用玩家分路；盲选查不到分路时按敌方常见分路兜底
        query_lane = lane or self._guess_lane(enemy_id)
        return self.recommender.recommend(enemy_id, query_lane)

    def _guess_lane(self, champion_id: int) -> str:
        """分路未知时，根据英雄定位(tags)粗略猜测其分路。"""
        info = self.db.by_champion_id(champion_id)
        if not info:
            return "top"
        tags = [t.lower() for t in info.get("tags", [])]
        # 极粗略兜底：Fighter/Tank 多为上单，Marksman 下路，Mage/Support 视情况
        if "marksman" in tags:
            return "bottom"
        if "support" in tags:
            return "support"
        if "fighter" in tags or "tank" in tags:
            return "top"
        if "assassin" in tags:
            return "jungle"
        return "middle"
