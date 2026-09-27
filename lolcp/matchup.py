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


# 缓存格式版本：修正 lolalytics 胜率语义后，旧缓存必须作废
CACHE_VERSION = "v2"


# ---------------- 缓存 ----------------
def _cache_path(provider: str, enemy_slug: str, lane: str) -> Path:
    return CACHE_DIR / f"{provider}_{CACHE_VERSION}_{enemy_slug}_{lane}.json"


def _cache_read(provider: str, enemy_slug: str, lane: str) -> Optional[list]:
    p = _cache_path(provider, enemy_slug, lane)
    if p.exists():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - obj.get("ts", 0) < CACHE_TTL:
                return obj["counters"]
        except Exception:
            pass
    return None


def _cache_write(provider: str, enemy_slug: str, lane: str, counters: list) -> None:
    try:
        _cache_path(provider, enemy_slug, lane).write_text(
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


def fetch_lolalytics(enemy_slug: str, lane: str, timeout: float = 12.0) -> Optional[List[dict]]:
    """返回 [{enemy_name, winrate, score}]。

    winrate = 敌方目标英雄对位该推荐英雄时的胜率（%），越低代表该推荐英雄
    越克制目标；score = 归一化克制幅度（负数=克制，越小越克制），用于排序。
    """
    url = f"https://lolalytics.com/lol/{enemy_slug}/counters/?lane={lane}"
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
def fetch_blitz(enemy_slug: str, lane: str, timeout: float = 12.0) -> Optional[List[dict]]:
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
class Recommender:
    def __init__(self, db: ChampionDB, providers: List[str], timeout: float = 12.0,
                 top_n: int = 8):
        self.db = db
        self.providers = providers
        self.timeout = timeout
        self.top_n = top_n

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
            rows = _cache_read(name, info["slug"], ql)
            if rows:
                src = "lolalytics" if name == "lola" else "blitz"
                recs = self._build_counters(enemy_id, ql, rows, src)
                if recs:
                    return recs, src
        return [], ""

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

        def work():
            info = self.db.by_champion_id(enemy_id)
            if not info:
                return
            slug, en = info["slug"], info["en"]
            ql = lane or "top"

            box: dict = {}

            def try_lola():
                try:
                    cached = _cache_read("lola", slug, ql)
                    if cached is not None:
                        box["lola"] = cached
                        return
                    rows = fetch_lolalytics(slug, ql, quick_timeout or self.timeout)
                    if rows:
                        _cache_write("lola", slug, ql, rows)
                        box["lola"] = rows
                except Exception:
                    pass

            def try_blitz():
                try:
                    cached = _cache_read("blitz", slug, ql)
                    if cached is not None:
                        box["blitz"] = cached
                        return
                    rows = fetch_blitz(slug, ql, quick_timeout or self.timeout)
                    if rows:
                        _cache_write("blitz", slug, ql, rows)
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
