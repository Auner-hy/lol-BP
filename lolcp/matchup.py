"""对位数据源与 Counter 推荐引擎。

数据源（按 config.data_providers 顺序尝试，自动降级）：
1. lolalytics —— 解析 https://lolalytics.com/lol/{enemy}/counters/?lane={lane}
   页面内嵌的对位胜率文本（已验证可用）；
2. blitz      —— 解析 https://blitz.gg/lol/champions/{enemy}/counters 内嵌 JSON（best-effort）；
3. offline    —— 内置离线克制表（offline_data.py）。

注意：WeGame/101.qq.com 的对位克制数据没有公开 API（仅集成在 WeGame 客户端内），
其客户端原理同样是读取 LCU 接口 + 腾讯私有统计；本工具用公开统计源替代，
国内网络访问 Lolalytics 失败时自动降级到离线表。
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests

from .config import CACHE_DIR
from .offline_data import OFFLINE_COUNTERS
from .champions import ChampionDB
from .lanes import belongs_to_lane

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


# 按站点复用 Session：同一站点的多次请求共用一条 TCP+TLS 连接，
# 省去每次重复的 DNS 解析与加密握手开销（实测可省 0.4~1.3 秒/次）
_SESSIONS: dict[str, requests.Session] = {}


def _session(name: str) -> requests.Session:
    s = _SESSIONS.get(name)
    if s is None:
        s = _make_session()
        _SESSIONS[name] = s
    return s


CACHE_TTL = 6 * 3600  # 对位数据缓存 6 小时


@dataclass
class Counter:
    champion_id: int
    name: str            # 显示名（中文优先）
    enemy_winrate: float # 被克制英雄（敌方目标）在该对位中的胜率（%），越低越克制
    games: int = 0       # 样本场次（0 表示未知）
    source: str = ""
    counter_score: float = 0.0  # 归一化克制幅度（<0 克制），仅用于排序

    @property
    def my_winrate(self) -> float:
        """我（推荐英雄）在该对位中的胜率（%），越高越克制。"""
        return round(100.0 - self.enemy_winrate, 2)


# 缓存格式版本：缓存文件名加入段位维度后，旧缓存必须作废
CACHE_VERSION = "v3"


# ---------------- 缓存 ----------------
def _cache_path(provider: str, enemy_slug: str, lane: str, tier: str) -> Path:
    return CACHE_DIR / f"{provider}_{CACHE_VERSION}_{enemy_slug}_{lane}_{tier}.json"


def _cache_read(provider: str, enemy_slug: str, lane: str, tier: str) -> Optional[list]:
    p = _cache_path(provider, enemy_slug, lane, tier)
    if p.exists():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - obj.get("ts", 0) < CACHE_TTL:
                return obj["counters"]
        except Exception:
            pass
    return None


def _cache_write(provider: str, enemy_slug: str, lane: str, tier: str,
                 counters: list) -> None:
    try:
        _cache_path(provider, enemy_slug, lane, tier).write_text(
            json.dumps({"ts": time.time(), "counters": counters}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


# ---------------- Provider: Lolalytics ----------------
# 每个对手卡片（Qwik SSR）包含三段关键文本：
#   1) "<目标英雄> wins against <对手> <WR>% of the time"  —— 目标英雄对位胜率
#   2) "After normalising ... wins against <对手> <Δ>% less/more often than
#       would be expected"                                —— 归一化克制幅度
#   3) "The average opponent winrate against <对手> is <avg>%."（全局胜率，不用）
# 注意：卡片在响应式布局里会重复输出两份，按对手名去重。
_LOLA_RE_WR = re.compile(
    r'wins against\s+<!--t=[^>]*-->\s*([A-Z][^<]+?)\s*<!--.*?'
    r'text-green-300[^>]*>\s*([0-9]+\.[0-9]+)%', re.S)
_LOLA_RE_NORM = re.compile(
    r'After normalising.*?text-yellow-100[^>]*>\s*([0-9]+\.[0-9]+)%</span>\s*'
    r'<!--t=[^>]*-->\s*(less|more)', re.S)
_LOLA_RE_CARD = re.compile(
    r'alt="([A-Z][^"]+)"[^>]*class="rounded border', re.S)


def fetch_lolalytics(enemy_slug: str, lane: str, timeout: float = 12.0,
                     tier: str = "emerald_plus") -> Optional[List[dict]]:
    """返回 [{enemy_name, winrate, score}]。

    winrate = 敌方目标英雄对位该推荐英雄时的胜率（%），越低代表该推荐英雄
    越克制目标；score = 归一化克制幅度（负数=克制，越小越克制），用于排序。
    tier = 段位筛选（gold_plus/platinum_plus/emerald_plus/diamond_plus/
    master_plus，已实测全部为有效参数）。
    """
    url = (f"https://lolalytics.com/lol/{enemy_slug}/counters/"
           f"?lane={lane}&tier={tier}")
    r = _session("lola").get(
        url, headers={"Accept-Language": "en;q=0.9"}, timeout=timeout)
    r.raise_for_status()
    page = r.text
    anchors = [m.start() for m in _LOLA_RE_CARD.finditer(page)]
    if not anchors:
        return None
    anchors.append(len(page))
    out, seen = [], set()
    for i in range(len(anchors) - 1):
        block = page[anchors[i]:anchors[i + 1]]
        m1 = _LOLA_RE_WR.search(block)
        if not m1:
            continue
        name = htmllib.unescape(m1.group(1)).strip()
        if name in seen:
            continue
        seen.add(name)
        try:
            wr = float(m1.group(2))
        except ValueError:
            continue
        m2 = _LOLA_RE_NORM.search(block)
        if m2:
            try:
                delta = float(m2.group(1))
                score = -delta if m2.group(2) == "less" else delta
            except ValueError:
                score = wr - 50.0
        else:
            score = wr - 50.0
        out.append({"enemy_name": name, "winrate": wr, "score": score})
    return out or None


# ---------------- Provider: Blitz (best effort) ----------------
def fetch_blitz(enemy_slug: str, lane: str, timeout: float = 12.0,
                tier: str = "emerald_plus") -> Optional[List[dict]]:
    # tier 仅用于缓存分桶（Blitz 页面不分段位），不参与请求 URL
    url = f"https://blitz.gg/lol/champions/{enemy_slug}/counters?role={lane.upper()}"
    try:
        r = _session("blitz").get(url, timeout=timeout)
        r.raise_for_status()
        html = r.text
    except Exception:
        return None
    # Blitz 把数据放在 __NEXT_DATA__ JSON 中；结构随版本变化，做尽力解析
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None

    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            # 常见字段：matchups / counters，内含 championName + winRate
            cname = node.get("championName") or node.get("name")
            wr = node.get("winRate") or node.get("winrate") or node.get("counterWinRate")
            if cname and isinstance(wr, (int, float)):
                # Blitz 的 counter winRate 一般是"敌方 vs 该英雄"的胜率
                found.append({"enemy_name": htmllib.unescape(str(cname)),
                              "winrate": float(wr), "score": float(wr) - 50.0})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    # 去重
    seen, uniq = set(), []
    for f in found:
        if f["enemy_name"] not in seen:
            seen.add(f["enemy_name"])
            uniq.append(f)
    return uniq or None


# ---------------- Provider: 离线表 ----------------
def fetch_offline(enemy_en: str, lane: str) -> Optional[List[dict]]:
    table = OFFLINE_COUNTERS.get(lane or "top", {})
    rows = table.get(enemy_en)
    if not rows:
        return None
    return [{"enemy_name": rec_en, "winrate": wr, "score": wr - 50.0}
            for rec_en, wr in rows]


# ---------------- 推荐引擎 ----------------
def fetch_hero_rows(db: ChampionDB, hero_id: int, lane: str, tier: str,
                    timeout: float = 5.0) -> tuple[Optional[list], str]:
    """拿某英雄在指定分路的整页对位行：先读磁盘缓存，未命中再发请求并写缓存。
    返回 (rows, source)；全失败返回 (None, "")。供全路总览批量查询使用。"""
    info = db.by_champion_id(hero_id)
    if not info:
        return None, ""
    slug, ql = info["slug"], lane or "top"
    cached = _cache_read("lola", slug, ql, tier)
    if cached is not None:
        return cached, "lolalytics"
    try:
        rows = fetch_lolalytics(slug, ql, timeout, tier)
        if rows:
            _cache_write("lola", slug, ql, tier, rows)
            return rows, "lolalytics"
    except Exception:
        pass
    cached = _cache_read("blitz", slug, ql, tier)
    if cached is not None:
        return cached, "blitz"
    try:
        rows = fetch_blitz(slug, ql, timeout, tier)
        if rows:
            _cache_write("blitz", slug, ql, tier, rows)
            return rows, "blitz"
    except Exception:
        pass
    return None, ""


# ============================= 通用符文 / 出装 =============================
# Data Dragon / Community Dragon 都是 Riot 官方公开静态资源（无需 key）
# 符文图标在 Community Dragon；装备/召唤师技能图标在 Data Dragon。
_DD_VERSION = "16.19.1"   # 图标版本（静态资源，稳定）
_PERKS_URL = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
              "global/default/v1/perks.json")
_SUMMONER_URL = (f"https://ddragon.leagueoflegends.com/cdn/{_DD_VERSION}"
                 "/data/en_US/summoner.json")
_CD_BASE = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
            "global/default/")
_PERK_ICON_URLS: dict[int, str] = {}   # 符文ID -> 图标URL（首次使用时加载）
_SUMMONER_ICON: dict[str, str] = {}    # 召唤师技能数字ID -> 图标文件名
_BOOT_IDS: set[int] = set()            # 鞋类装备ID（由 Data Dragon 标签识别）


@dataclass
class BuildInfo:
    """单个英雄单分路的通用推荐（不区分对位）。所有 *_url 为图标地址。"""
    champion_id: int
    lane: str
    patch: str
    keystone_id: int
    keystone_url: str
    runes: List[dict]          # [{"id","url","tree"}] 基石之外的小符文
    shards: List[str]          # 3 个碎片的图标URL
    summoner: List[str]        # 2 个召唤师技能图标URL
    start_items: List[str]     # 出门装图标URL
    core_items: List[str]      # 核心装图标URL（3件）
    boots_url: str             # 鞋子图标URL
    skill_order: List[str]     # 前5级加点，元素 Q/W/E/R
    games: int
    win_rate: float


def _item_url(item_id) -> str:
    """装备数字ID -> Data Dragon 图标URL（兼容字符串ID）。"""
    return (f"https://ddragon.leagueoflegends.com/cdn/{_DD_VERSION}"
            f"/img/item/{item_id}.png")


def _ensure_perk_map() -> None:
    """加载 符文ID->图标URL 映射（带磁盘缓存，失败则跳过）。"""
    if _PERK_ICON_URLS:
        return
    rows = _cache_read("perks", "map", "all", "v1")
    if not rows:
        try:
            j = _session("cd").get(_PERKS_URL, timeout=15).json()
            rows = [{"id": p["id"], "path": p["iconPath"]} for p in j]
            _cache_write("perks", "map", "all", "v1", rows)
        except Exception:
            rows = []
    for r in rows:
        p = r["path"].replace("/lol-game-data/assets/", "")
        _PERK_ICON_URLS[int(r["id"])] = _CD_BASE + p.lower()


def _perk_url(perk_id: int) -> str:
    _ensure_perk_map()
    return _PERK_ICON_URLS.get(int(perk_id), "")


def _shard_url(shard_id) -> str:
    """属性碎片(50xx)图标：与符文共用 Community Dragon 映射。"""
    return _perk_url(int(shard_id))


def _ensure_summoner_map() -> None:
    """加载 召唤师技能数字ID->图标文件名 映射。"""
    if _SUMMONER_ICON:
        return
    rows = _cache_read("summon", "map", "all", "v1")
    if not rows:
        try:
            j = _session("cd").get(_SUMMONER_URL, timeout=15).json()["data"]
            rows = [{"key": v["key"], "id": v["id"]} for v in j.values()]
            _cache_write("summon", "map", "all", "v1", rows)
        except Exception:
            rows = []
    for r in rows:
        _SUMMONER_ICON[str(r["key"])] = r["id"]


def _summoner_url(spell_id) -> str:
    _ensure_summoner_map()
    name = _SUMMONER_ICON.get(str(spell_id))
    if not name:
        return ""
    return (f"https://ddragon.leagueoflegends.com/cdn/{_DD_VERSION}"
            f"/img/spell/{name}.png")


def _ensure_boot_set() -> None:
    """加载鞋类装备ID集合（Data Dragon item 数据中 tags 含 BOOTS）。"""
    if _BOOT_IDS:
        return
    rows = _cache_read("boots", "map", "all", "v1")
    if not rows:
        try:
            u = (f"https://ddragon.leagueoflegends.com/cdn/{_DD_VERSION}"
                 "/data/en_US/item.json")
            j = _session("cd").get(u, timeout=15).json()["data"]
            rows = [int(k) for k, v in j.items()
                    if "BOOTS" in [t.upper() for t in (v.get("tags") or [])]]
            _cache_write("boots", "map", "all", "v1", rows)
        except Exception:
            rows = []
    _BOOT_IDS.update(int(r) for r in rows)


def _top_pick(rows: List[dict], key: str):
    """从 [{pick_rate,...}] 中取选择率最高的一项（无 pick_rate 返回 None）。"""
    if not rows:
        return None
    return max(rows, key=lambda x: x.get("pick_rate") or 0)


def fetch_build(champion_id: int, slug: str, lane: str,
                timeout: float = 12.0) -> Optional[BuildInfo]:
    """抓取并解析某英雄某分路的通用符文出装（Blitz 页面预取的明文 JSON）。

    slug 为 Blitz 使用的英雄标识（= Data Dragon 内部英雄 key 小写，
    如悟空 monkeyking、蕾娜塔 renata），由调用方从 ChampionDB 取得。
    """
    url = f"https://blitz.gg/lol/champions/{slug}/build?lane={lane}"
    r = _session("blitz").get(url, timeout=timeout)
    if r.status_code != 200:
        return None
    m = re.search(
        r'<script type="application/json" data-sveltekit-fetched[^>]*'
        r'data-url="[^"]*champion_builds_tags[^"]*"[^>]*>(.*?)</script>',
        r.text, re.S)
    if not m:
        return None
    wrap = json.loads(m.group(1))
    rows = json.loads(wrap["body"]).get("data", [])
    if not rows:
        return None
    # Blitz 通常按热度返回几套 build，取对局样本最多的一套作为主流推荐
    s = max(rows, key=lambda x: x.get("games") or 0)

    # 基石符文
    ks = _top_pick(s.get("keystone") or [], "keystone_id")
    keystone_id = int(ks["keystone_id"]) if ks else 0
    # 小符文：按 index(槽位) 取每槽最热，排除基石槽(index=0)
    by_index: dict[int, list] = {}
    for rn in s.get("runes") or []:
        by_index.setdefault(rn.get("index", 0), []).append(rn)
    small = []
    for idx in sorted(by_index):
        if idx == 0:
            continue
        top = _top_pick(by_index[idx], "runeId")
        if top:
            rid = int(top["runeId"])
            small.append({"id": rid, "url": _perk_url(rid),
                          "tree": top.get("treeId", 0)})
    # 碎片（3个：进攻/灵活/防御）
    shard_urls: List[str] = []
    for shard_key in ["offenseShard", "flexShard", "defenseShard"]:
        top = _top_pick((s.get("shards") or {}).get(shard_key) or [], "shard_id")
        if top:
            shard_urls.append(_shard_url(top["shard_id"]))
    # 召唤师技能
    sum_top = _top_pick(s.get("summonerSpells") or [], "summonerSpellIds")
    summoner_urls = []
    if sum_top:
        summoner_urls = [_summoner_url(i) for i in sum_top["summonerSpellIds"]]
    # 出门装（itemIds 为字符串ID列表；2003=生命药水、3340=视野守卫，均不单独展示）
    _HIDE_START = {"2003", "3340"}
    start_top = _top_pick(s.get("startingItems") or [], "itemIds")
    start_urls = []
    if start_top:
        start_urls = [_item_url(i) for i in start_top["itemIds"]
                      if str(i) not in _HIDE_START]
    # 核心装（Blitz 的 coreItems 有时本身含鞋；鞋要单独展示，先分离出来）
    _ensure_boot_set()
    core_top = _top_pick(s.get("coreItems") or [], "itemIds")
    core_urls = []
    core_boot_id = 0
    if core_top:
        core_raw = [int(x) for x in str(core_top["itemIds"]).split(",") if x]
        for i in core_raw:
            if i in _BOOT_IDS and not core_boot_id:
                core_boot_id = i
            else:
                core_urls.append(_item_url(i))
    # 鞋子：优先核心装里的鞋，否则从情境装备选选择率最高的一双
    boot_id = core_boot_id
    boot_score = -1.0
    for sit in s.get("situationalItems") or []:
        iid = int(sit.get("itemId", 0))
        if iid in _BOOT_IDS:
            score = sit.get("games", 0)
            if score > boot_score:
                boot_id, boot_score = iid, score
    boots = _item_url(boot_id) if boot_id else ""
    # 技能加点：取最热 skillOrder 的前5级
    skill = _top_pick(s.get("skillOrders") or [], "skillOrder")
    skill5 = []
    if skill:
        skill5 = [["Q", "W", "E", "R"][i - 1]
                  for i in skill["skillOrder"][:5] if 1 <= i <= 4]

    # win_rate 源是 0~1 的比例，统一归一到百分数；个别来源可能已给 0~100
    wr_raw = float(s.get("win_rate") or 0.0)
    win_rate_pct = wr_raw * 100.0 if wr_raw <= 1.0 else wr_raw
    return BuildInfo(
        champion_id=champion_id, lane=lane, patch=str(s.get("patch", "")),
        keystone_id=keystone_id, keystone_url=_perk_url(keystone_id),
        runes=small, shards=shard_urls, summoner=summoner_urls,
        start_items=start_urls, core_items=core_urls, boots_url=boots,
        skill_order=skill5, games=int(s.get("games") or 0),
        win_rate=win_rate_pct)


class Recommender:
    def __init__(self, db: ChampionDB, providers: List[str], timeout: float = 12.0,
                 top_n: int = 8, tier: str = "emerald_plus"):
        self.db = db
        self.providers = providers
        self.timeout = timeout
        self.top_n = top_n
        # 段位筛选：UI 切换时直接改此属性并触发重查，无需重建 Recommender
        self.tier = tier

    # ---------- 离线秒出（无网络等待） ----------
    def recommend_offline(self, enemy_id: int, lane: str) -> tuple[List[Counter], str]:
        """只用内置离线表，立即返回（无结果则返回空）。"""
        info = self.db.by_champion_id(enemy_id)
        if not info:
            return [], ""
        rows = fetch_offline(info["en"], lane)
        if rows:
            return self._build_counters(enemy_id, lane, rows, "离线表"), "离线表"
        return [], ""

    def recommend_cached(self, enemy_id: int, lane: str) -> tuple[List[Counter], str]:
        """只用磁盘缓存，立即返回（缓存未过期时毫秒出结果，不发任何网络请求）。

        未命中返回 ([], "")。缓存名 lola/blitz 与数据源名一致。
        """
        info = self.db.by_champion_id(enemy_id)
        if not info:
            return [], ""
        ql = lane or "top"
        # 缓存名 -> 数据源配置名（磁盘文件用简称 lola，配置里是全称 lolalytics）
        name_map = {"lola": "lolalytics", "blitz": "blitz"}
        for name in ("lola", "blitz"):
            if name_map[name] not in self.providers:
                continue
            rows = _cache_read(name, info["slug"], ql, self.tier)
            if rows:
                src = "lolalytics" if name == "lola" else "blitz"
                recs = self._build_counters(enemy_id, ql, rows, src)
                if recs:
                    return recs, src
        return [], ""

    # ---------- 通用符文 / 出装 ----------
    @staticmethod
    def _build_to_dict(b: BuildInfo) -> dict:
        return {k: getattr(b, k) for k in [
            "champion_id", "lane", "patch", "keystone_id", "keystone_url",
            "runes", "shards", "summoner", "start_items", "core_items",
            "boots_url", "skill_order", "games", "win_rate"]}

    @staticmethod
    def _build_from_dict(d: dict) -> BuildInfo:
        data = {k: d.get(k) for k in [
            "champion_id", "lane", "patch", "keystone_id", "keystone_url",
            "runes", "shards", "summoner", "start_items", "core_items",
            "boots_url", "skill_order", "games", "win_rate"]}
        # 兼容旧缓存：win_rate 可能是 0~1 比例
        wr = data.get("win_rate") or 0.0
        if wr <= 1.0:
            data["win_rate"] = wr * 100.0
        # 兼容旧缓存：核心装里若混进了鞋子，去掉以免和单独的鞋重复
        bu = data.get("boots_url")
        if bu and data.get("core_items"):
            data["core_items"] = [u for u in data["core_items"] if u != bu]
        return BuildInfo(**data)

    def build_cached(self, champion_id: int, lane: str) -> Optional[BuildInfo]:
        """只用磁盘缓存返回符文出装（未命中返回 None，不发网络请求）。"""
        info = self.db.by_champion_id(champion_id)
        if not info:
            return None
        ql = lane or "top"
        rows = _cache_read("build", info["slug"], ql, "v1")
        if rows:
            return self._build_from_dict(rows)
        return None

    def build_fetch(self, champion_id: int, lane: str) -> Optional[BuildInfo]:
        """在线抓取符文出装并写缓存（失败返回 None）。"""
        info = self.db.by_champion_id(champion_id)
        if not info:
            return None
        ql = lane or "top"
        b = fetch_build(champion_id, info["slug"], ql, self.timeout)
        if b and b.keystone_id:
            _cache_write("build", info["slug"], ql, "v1",
                         self._build_to_dict(b))
            return b
        return None

    def recommend(self, enemy_id: int, lane: str) -> tuple[List[Counter], str]:
        """同步推荐：在线源竞速（超时即降级），适合 CLI。GUI 请用异步接口。"""
        result = {}

        def cb(recs, src):
            result["recs"], result["src"] = recs, src

        t = self.recommend_async(enemy_id, lane, on_online=cb, quick_timeout=None)
        t.join(timeout=self.timeout + 3)
        if result:
            return result["recs"], result["src"]
        return self.recommend_offline(enemy_id, lane)

    def recommend_async(self, enemy_id: int, lane: str,
                        on_online=None, quick_timeout: float | None = 8.0):
        """后台线程拉取在线数据（lolalytics/blitz 并行竞速）。

        on_online(recs, source)：拿到在线数据后在后台线程回调（可能很快、
        也可能网络失败永不回调，调用方负责超时/丢弃）。quick_timeout 限制
        单次在线尝试时长。返回 threading.Thread（已 start，daemon）。
        """
        import threading

        # 捕获本次请求的段位：防止请求途中 UI 切换段位导致读写错位
        tier = self.tier

        def work():
            info = self.db.by_champion_id(enemy_id)
            if not info:
                return
            slug, en = info["slug"], info["en"]
            ql = lane or "top"

            box: dict = {}

            def try_lola():
                try:
                    cached = _cache_read("lola", slug, ql, tier)
                    if cached is not None:
                        box["lola"] = cached
                        return
                    rows = fetch_lolalytics(slug, ql, quick_timeout or self.timeout, tier)
                    if rows:
                        _cache_write("lola", slug, ql, tier, rows)
                        box["lola"] = rows
                except Exception:
                    pass

            def try_blitz():
                try:
                    cached = _cache_read("blitz", slug, ql, tier)
                    if cached is not None:
                        box["blitz"] = cached
                        return
                    rows = fetch_blitz(slug, ql, quick_timeout or self.timeout, tier)
                    if rows:
                        _cache_write("blitz", slug, ql, tier, rows)
                        box["blitz"] = rows
                except Exception:
                    pass

            jobs = []
            if "lolalytics" in self.providers:
                t1 = threading.Thread(target=try_lola, daemon=True)
                t1.start(); jobs.append(t1)
            if "blitz" in self.providers:
                t2 = threading.Thread(target=try_blitz, daemon=True)
                t2.start(); jobs.append(t2)
            # 共享截止时间：两个线程共用同一个超时上限，避免逐个 join
            # 导致最坏情况下等待翻倍（旧逻辑最坏需等 2*timeout）
            deadline = time.time() + (quick_timeout or self.timeout)
            for t in jobs:
                remaining = max(0.0, deadline - time.time())
                t.join(timeout=remaining)

            rows = box.get("lola") or box.get("blitz")
            if rows:
                src = "lolalytics" if "lola" in box else "blitz"
                recs = self._build_counters(enemy_id, ql, rows, src)
                if on_online and recs:
                    on_online(recs, src)

        t = threading.Thread(target=work, daemon=True)
        t.start()
        return t

    def _build_counters(self, enemy_id: int, lane: str, rows: list, source: str) -> List[Counter]:
        counters: List[Counter] = []
        for row in rows:
            name = htmllib.unescape(str(row["enemy_name"])).strip()
            cid = self.db.id_by_en(name)
            if cid is None or cid == enemy_id:
                continue
            info = self.db.by_champion_id(cid)
            en_name = info["en"] if info else name
            # 只保留同分路英雄（未知英雄宽松保留）
            if lane and not belongs_to_lane(en_name, lane):
                continue
            wr = float(row["winrate"])
            counters.append(Counter(
                champion_id=cid,
                name=info["name"] if info else name,
                enemy_winrate=wr,
                games=int(row.get("games", 0) or 0),
                source=source,
                counter_score=float(row.get("score", wr - 50.0)),
            ))
        # 排序：严格按对位胜率——敌方胜率越低（=我方对位胜率越高）越靠前；
        # 胜率相同时再用归一化克制幅度细分，保证同率次序稳定
        counters.sort(key=lambda c: (c.enemy_winrate, c.counter_score))
        return counters[: self.top_n]
