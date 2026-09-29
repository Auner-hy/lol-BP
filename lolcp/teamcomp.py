"""阵容分析引擎（team composition）：看"整个阵容"而不只是看对线。

把双方已选英雄的属性（champion_traits 软属性 + 官方 stats 身板）汇总，
用规则判断我方当前最缺什么、最怕什么，再给每个同分路候选英雄打"团队契合分"。

注意定位：这是**经验启发式规则（heuristic）**，给出"合不合理"，
Riot 没有任意阵容对任意阵容的胜率数据，所以这里**不输出胜率、不保证输赢**。
每条推荐都带可读理由，方便人工判断规则判得对不对。

数据流：
  双方英雄ID -> TeamProfile(阵容画像) -> 需求清单 needs -> 候选英雄契合打分
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import champion_traits as ct
from . import lanes as lanes_mod


# ---- 参考评估等级：用 6 级(约中期)数值衡量身板，避免只看 1 级数值偏差 ----
EVAL_LEVEL = 6


# ============================ 数据结构 ============================
@dataclass
class TeamProfile:
    """一方阵容的统计画像：各类定位人数、伤害构成、身板、位移/开团情况。"""
    size: int = 0
    n_tank: int = 0          # 坦克 / 前排定位
    n_fighter: int = 0       # 战士
    n_assassin: int = 0      # 刺客
    n_mage: int = 0          # 法师
    n_marksman: int = 0      # 射手
    n_support: int = 0       # 辅助定位
    n_ad_damage: int = 0     # 主要物理伤害
    n_ap_damage: int = 0     # 主要魔法伤害
    n_mixed_damage: int = 0  # 双伤害
    n_burst_assassin: int = 0   # 高爆发突进威胁（刺客型）
    n_ranged: int = 0        # 远程英雄数
    n_mobile: int = 0        # 有位移的英雄数
    n_hard_engage: int = 0   # 强开团英雄数
    avg_tankiness: float = 0.0  # 平均身板分（越高越肉）


@dataclass
class TeamNeed:
    """一条阵容需求：类型、强度(0~1)、给界面的中文说明。"""
    kind: str
    strength: float
    message: str


@dataclass
class TeamRec:
    """一条阵容推荐英雄：契合分 + 命中的需求标签 + 推荐理由。"""
    champion_id: int
    fit: float                  # 团队契合分 0~100
    reasons: List[str] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)  # 命中的需求 kind


@dataclass
class TeamCompResult:
    """阵容分析完整结果：双方画像、需求清单、推荐列表、是否数据充足。"""
    ours: TeamProfile
    theirs: TeamProfile
    needs: List[TeamNeed]
    recs: List[TeamRec]
    ready: bool                 # 数据是否足以分析
    notice: str = ""            # ready=False 时的提示语


# ============================ 身板 / 属性计算 ============================
def _stats_at_level(stats: dict, level: int) -> dict:
    """把官方 1 级基础属性按成长值折算到指定等级（仅算阵容分析需要的几项）。"""
    if not stats:
        return {}
    dl = max(0, level - 1)

    def v(k, pk=None):
        base = float(stats.get(k, 0) or 0)
        grow = float(stats.get(pk or "", 0) or 0)
        return base + grow * dl
    return {
        "hp": v("hp", "hp_per_level"),
        "armor": v("armor", "armor_per_level"),
        "spellblock": v("spellblock", "spellblock_per_level"),
        "attackrange": float(stats.get("attackrange", 0) or 0),
    }


def tankiness_score(stats: dict, level: int = EVAL_LEVEL) -> float:
    """根据中期血量 + 有效护甲 + 有效魔抗估算身板分（仅用于横向比较）。

    用 1000 + 护甲 把减伤换算成等效血量，再取护甲/魔抗两方向的较小值
    （取短板，避免单堆一种抗性虚高）。
    """
    s = _stats_at_level(stats, level)
    if not s:
        return 0.0
    hp = s.get("hp", 0)
    eff_phys = hp * (1000 + s.get("armor", 0)) / 1000.0
    eff_magic = hp * (1000 + s.get("spellblock", 0)) / 1000.0
    return round(min(eff_phys, eff_magic), 1)


def is_ranged(stats: dict) -> bool:
    """根据攻击距离判断远程（>300 视为远程，覆盖多数法师/射手）。"""
    return float((stats or {}).get("attackrange", 0) or 0) > 300


def _primary_role(tags: List[str], traits: tuple) -> str:
    """归一个主定位（用于阵容人数统计）。

    Riot 的 tags 是有序的：第一个就是主定位、第二个是次要定位
    （如李青 ['Fighter','Assassin'] 主定位是战士）。
    直接取首个标签最准确，不认识时兜底为战士。
    """
    primary = tags[0] if tags else "Fighter"
    mapping = {
        "Tank": "tank",
        "Fighter": "fighter",
        "Assassin": "assassin",
        "Mage": "mage",
        "Marksman": "marksman",
        "Support": "support",
    }
    return mapping.get(primary, "fighter")


# ============================ 阵容画像 ============================
def build_profile(db, cids: List[int]) -> TeamProfile:
    """根据一方英雄 ID 列表，统计出阵容画像 TeamProfile。"""
    p = TeamProfile()
    tank_scores = []
    for cid in cids:
        info = db.by_champion_id(cid)
        if not info:
            continue
        p.size += 1
        tags = info.get("tags", [])
        stats = info.get("stats", {})
        traits = ct.traits_of(cid)
        damage, style, mob, eng, cc = traits

        role = _primary_role(tags, traits)
        setattr(p, f"n_{role}", getattr(p, f"n_{role}") + 1)

        # 伤害构成
        if damage == ct.AD:
            p.n_ad_damage += 1
        elif damage == ct.AP:
            p.n_ap_damage += 1
        else:
            p.n_mixed_damage += 1

        # 爆发突进威胁
        if ct.is_assassin_burst(cid):
            p.n_burst_assassin += 1
        if is_ranged(stats):
            p.n_ranged += 1
        if ct.has_escape(cid):
            p.n_mobile += 1
        if ct.is_hard_engage(cid):
            p.n_hard_engage += 1
        ts = tankiness_score(stats)
        if ts:
            tank_scores.append(ts)

    if tank_scores:
        p.avg_tankiness = round(sum(tank_scores) / len(tank_scores), 1)
    return p


# ============================ 需求判断（规则） ============================
def derive_needs(ours: TeamProfile, theirs: TeamProfile) -> List[TeamNeed]:
    """根据双方画像，推出我方阵容当前的需求清单（带强度与说明）。"""
    needs: List[TeamNeed] = []

    # ① 缺前排：我方没有坦克、或战士很少且平均身板偏脆
    frontline = ours.n_tank + ours.n_fighter
    if ours.size >= 2 and ours.n_tank == 0:
        strength = min(1.0, 0.55 + 0.15 * max(0, 3 - frontline))
        needs.append(TeamNeed(
            "frontline", strength,
            f"我方还没有坦克前排（坦克0/战士{ours.n_fighter}），容易被冲脸、缺开团"))

    # ② 怕对面刺客 / 高爆发突进：对面爆发突进英雄 >=2
    if theirs.n_burst_assassin >= 2:
        strength = min(1.0, 0.5 + 0.2 * theirs.n_burst_assassin)
        needs.append(TeamNeed(
            "anti_assassin", strength,
            f"对面有 {theirs.n_burst_assassin} 个高爆发突进（刺客型），"
            "需要保命强、有位移/护盾、能拉扯的英雄"))

    # ③ 伤害类型失衡：我方几乎全 AD 或全 AP，需要补另一类伤害
    dmg_total = ours.n_ad_damage + ours.n_ap_damage + ours.n_mixed_damage
    if dmg_total >= 3:
        if ours.n_ap_damage == 0 and ours.n_mixed_damage == 0:
            needs.append(TeamNeed(
                "need_ap", 0.7,
                f"我方 {ours.n_ad_damage} 人全是物理伤害，对面可无脑堆护甲，需要补 AP 伤害"))
        elif ours.n_ad_damage == 0 and ours.n_mixed_damage == 0:
            needs.append(TeamNeed(
                "need_ad", 0.7,
                f"我方 {ours.n_ap_damage} 人全是魔法伤害，对面可无脑堆魔抗，需要补 AD 伤害"))

    # ④ 缺开团：全队没有强开点（且已有一定人数）
    if ours.size >= 3 and ours.n_hard_engage == 0:
        needs.append(TeamNeed(
            "engage", 0.6,
            "我方没有能强开团的英雄，团战只能被动挨打，建议补一个开团点"))

    # ⑤ 对面物理伤害过多 -> 护甲坦克价值高
    if theirs.n_ad_damage >= 3:
        needs.append(TeamNeed(
            "armor_tank", 0.6,
            f"对面 {theirs.n_ad_damage} 人物理伤害为主，护甲坦克（如龙龟、石头人）收益高"))
    elif theirs.n_ap_damage >= 3:
        needs.append(TeamNeed(
            "mr_tank", 0.6,
            f"对面 {theirs.n_ap_damage} 人魔法伤害为主，需要魔抗前排"))

    # 按强度排序，强需求在前
    needs.sort(key=lambda n: n.strength, reverse=True)
    return needs


# ============================ 候选契合打分 ============================
def _candidate_matches(cid: int, tags: List[str], traits: tuple, stats: dict,
                       needs: List[TeamNeed]):
    """计算单个候选英雄对每条需求的命中情况，返回 (得分, 命中需求)。"""
    damage, style, mob, eng, cc = traits
    ranged = is_ranged(stats)
    tagset = set(tags)
    is_tank = "Tank" in tagset
    is_front = (not ranged) and (is_tank or "Fighter" in tagset)
    score = 0.0
    matched: List[str] = []

    for need in needs:
        k, w = need.kind, need.strength
        hit = 0.0
        if k == "frontline":
            # 补前排：坦克优先，近战能开团的战士也算
            if is_tank:
                hit = 1.0
            elif is_front and eng == ct.ENGAGE:
                hit = 0.8
        elif k == "anti_assassin":
            # 保命强：有位移 + 有控制，或反手保护型；偏肉前排也能扛
            if is_tank:
                hit = max(hit, 0.8)
            if eng == ct.PECK and (mob >= 1 or cc >= 1):
                hit = max(hit, 0.8)
            if mob >= 2 and cc >= 1:
                hit = max(hit, 0.6)
        elif k == "need_ap":
            hit = 1.0 if damage == ct.AP else (0.5 if damage == ct.MIX else 0)
        elif k == "need_ad":
            hit = 1.0 if damage == ct.AD else (0.5 if damage == ct.MIX else 0)
        elif k == "engage":
            hit = 1.0 if eng == ct.ENGAGE else 0
        elif k == "armor_tank":
            # AP 近战开团坦克（石头人/龙龟一类，龙龟实为AD也吃护甲），用坦克+近战判定
            if is_front:
                hit = 0.9
        elif k == "mr_tank":
            if is_front:
                hit = 0.85

        if hit > 0:
            score += w * hit
            matched.append(k)
    return score, matched


def score_candidates(db, needs: List[TeamNeed], lane: str,
                     exclude_ids: List[int]) -> List[TeamRec]:
    """对限定分路的全部候选英雄打分，返回按契合分排序的阵容推荐。"""
    exclude = set(exclude_ids)
    recs: List[TeamRec] = []
    for cid in db.all_ids():
        if cid in exclude:
            continue
        info = db.by_champion_id(cid)
        if not info:
            continue
        # 限定分路（未知分路英雄宽松放行，但这里要求必须有分路数据更稳）
        if lane and not lanes_mod.belongs_to_lane(info["en"], lane):
            continue
        traits = ct.traits_of(cid)
        stats = info.get("stats", {})
        score, matched = _candidate_matches(cid, info.get("tags", []),
                                            traits, stats, needs)
        if score <= 0 or not matched:
            continue
        fit = round(min(100.0, score * 60.0), 1)  # 映射到 0~100
        reasons = _build_reasons(matched, needs)
        recs.append(TeamRec(champion_id=cid, fit=fit, reasons=reasons,
                            matched=matched))
    recs.sort(key=lambda r: r.fit, reverse=True)
    return recs


def _build_reasons(matched: List[str], needs: List[TeamNeed]) -> List[str]:
    """根据命中的需求，生成给界面看的简短中文理由。"""
    msg_by_kind = {n.kind: n.message for n in needs}
    labels = {
        "frontline": "🛡 补充坦克前排",
        "anti_assassin": "💨 保命/位移强，克制刺客",
        "need_ap": "🔮 补充 AP 魔法伤害",
        "need_ad": "⚔ 补充 AD 物理伤害",
        "engage": "📣 提供强开团",
        "armor_tank": "🛡 高护甲克制物理阵容",
        "mr_tank": "🛡 魔抗前排克制法师",
    }
    return [labels.get(k, k) for k in matched]


# ============================ 对外入口 ============================
def analyze(db, ally_ids: List[int], enemy_ids: List[int], lane: str,
            my_pick_intent: int = 0) -> TeamCompResult:
    """阵容分析主入口。

    参数：
      db           英雄库 ChampionDB
      ally_ids     我方已选英雄 ID（不含自己将选的）
      enemy_ids    敌方已选英雄 ID
      lane         玩家本局分路（用于限定候选）
      my_pick_intent 玩家当前预选英雄 ID（避免把它再推荐一遍）
    """
    ours = build_profile(db, ally_ids)
    theirs = build_profile(db, enemy_ids)

    # 数据充足性：双方合计至少 4 人、且我方至少 2 人才有分析意义
    total = ours.size + theirs.size
    if total < 4 or ours.size < 1 or theirs.size < 1:
        return TeamCompResult(
            ours, theirs, [], [], ready=False,
            notice="阵容人数不足，等双方各选出 1~2 名英雄后开始分析阵容")

    needs = derive_needs(ours, theirs)
    if not needs:
        # 没有明显短板：给出一个中性结果（阵容较均衡）
        return TeamCompResult(
            ours, theirs, [], [], ready=True,
            notice="当前阵容没有明显短板，可优先按对位克制选择")

    exclude = list(ally_ids) + list(enemy_ids)
    if my_pick_intent:
        exclude.append(my_pick_intent)
    recs = score_candidates(db, needs, lane, exclude)
    return TeamCompResult(ours, theirs, needs, recs, ready=True)
