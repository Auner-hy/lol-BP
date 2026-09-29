"""英雄数据：通过 Riot 官方 Data Dragon 获取英雄 ID / 英文名 / 中文名映射，带本地缓存。

Data Dragon 是 Riot 官方公开静态数据（无需 key）：
  https://developer.riotgames.com/docs/lol#data-dragon
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Optional

import requests

from .config import CACHE_DIR

DDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPIONS = "https://ddragon.leagueoflegends.com/cdn/{version}/data/{lang}/champion.json"

# LCU / 游戏内部数字 ID -> 英雄英文名 的兜底映射（Data Dragon 失败时使用，覆盖常见英雄）
# 完整映射会在联网后自动补全。
FALLBACK_ID_TO_NAME: Dict[int, str] = {
    266: "Aatrox", 103: "Ahri", 84: "Akali", 166: "Akshan", 12: "Alistar",
    32: "Amumu", 34: "Anivia", 1: "Annie", 523: "Aphelios", 22: "Ashe",
    136: "AurelionSol", 268: "Azir", 432: "Bard", 200: "Bel'Veth", 53: "Blitzcrank",
    63: "Brand", 201: "Braum", 233: "Briar", 51: "Caitlyn", 164: "Camille",
    69: "Cassiopeia", 31: "Cho'Gath", 42: "Corki", 122: "Darius", 131: "Diana",
    119: "Draven", 36: "Dr. Mundo", 245: "Ekko", 60: "Elise", 28: "Evelynn",
    81: "Ezreal", 9: "Fiddlesticks", 114: "Fiora", 105: "Fizz", 3: "Galio",
    41: "Gangplank", 86: "Garen", 150: "Gnar", 79: "Gragas", 104: "Graves",
    887: "Gwen", 120: "Hecarim", 74: "Heimerdinger", 910: "Hwei", 420: "Illaoi",
    39: "Irelia", 427: "Ivern", 40: "Janna", 59: "Jarvan IV", 24: "Jax",
    126: "Jayce", 202: "Jhin", 222: "Jinx", 145: "Kai'Sa", 429: "Kalista",
    43: "Karma", 30: "Karthus", 38: "Kassadin", 55: "Katarina", 10: "Kayle",
    141: "Kayn", 85: "Kennen", 121: "Kha'Zix", 203: "Kindred", 240: "Kled",
    96: "Kog'Maw", 897: "K'Sante", 7: "LeBlanc", 64: "Lee Sin", 89: "Leona",
    876: "Lillia", 127: "Lissandra", 236: "Lucian", 117: "Lulu", 99: "Lux",
    54: "Malphite", 90: "Malzahar", 57: "Maokai", 11: "Master Yi", 801: "Mel",
    902: "Milio", 21: "Miss Fortune", 62: "Wukong", 82: "Mordekaiser", 25: "Morgana",
    950: "Naafiri", 267: "Nami", 75: "Nasus", 111: "Nautilus", 518: "Neeko",
    76: "Nidalee", 895: "Nilah", 56: "Nocturne", 20: "Nunu & Willump", 61: "Orianna",
    516: "Ornn", 80: "Pantheon", 78: "Poppy", 555: "Pyke", 246: "Qiyana",
    133: "Quinn", 497: "Rakan", 33: "Rammus", 421: "Rek'Sai", 526: "Rell",
    888: "Renata Glasc", 58: "Renekton", 107: "Rengar", 920: "Smolder", 72: "Riven",
    68: "Rumble", 13: "Ryze", 360: "Samira", 113: "Sejuani", 235: "Senna",
    147: "Seraphine", 875: "Sett", 35: "Shaco", 98: "Shen", 102: "Shyvana",
    27: "Singed", 14: "Sion", 15: "Sivir", 77: "Skarner", 37: "Sona",
    16: "Soraka", 50: "Swain", 517: "Sylas", 134: "Syndra", 223: "Tahm Kench",
    163: "Taliyah", 91: "Talon", 44: "Taric", 17: "Teemo", 412: "Thresh",
    18: "Tristana", 48: "Trundle", 23: "Tryndamere", 4: "Twisted Fate", 29: "Twitch",
    78: "Poppy", 6: "Urgot", 110: "Varus", 67: "Vayne", 45: "Veigar",
    161: "Vel'Koz", 711: "Vex", 254: "Vi", 234: "Viego", 112: "Viktor",
    8: "Vladimir", 106: "Volibear", 19: "Warwick", 498: "Xayah", 101: "Xerath",
    5: "Xin Zhao", 157: "Yasuo", 777: "Yone", 83: "Yorick", 350: "Yuumi",
    154: "Zac", 238: "Zed", 221: "Zeri", 115: "Ziggs", 26: "Zilean",
    142: "Zoe", 143: "Zyra",
}

# 玩家社区常用俗称 -> 英雄 ID（搜索/匹配时兼容）
COMMON_ALIASES: Dict[int, str] = {
    122: "诺手 洛克", 86: "德玛 大宝剑", 119: "德莱文 文森特",
    266: "剑魔", 245: "艾克", 64: "瞎子 盲僧", 238: "劫 儿童劫",
    222: "萝莉 暴走萝莉", 236: "卢锡安 奥巴马", 145: "卡莎",
    202: "烬 戏命师", 51: "女警 皮城", 523: "厄斐琉斯 月男",
    157: "索子哥 快乐风男", 777: "永恩", 103: "狐狸 阿狸",
    99: "光辉 光女", 268: "黄鸡 沙皇", 61: "发条", 134: "球女 辛德拉",
    84: "阿卡丽 阿卡利", 24: "武器 一灯大师", 39: "刀妹",
    54: "石头人 墨菲特", 150: "纳尔", 14: "塞恩 老司机", 27: "炼金 断头上单",
    82: "铁男 莫德凯撒", 75: "狗头", 78: "波比 锤形态小炮",
    875: "瑟提 腕豪 劲夫", 887: "格温 剪刀妹", 897: "奎桑提 黑哥",
    420: "俄洛伊 章鱼妈", 19: "狼人 哈士奇", 28: "寡妇 伊芙琳",
    121: "螳螂", 107: "狮子狗", 56: "梦魇 魔腾", 60: "蜘蛛",
    11: "流浪 光头", 163: "岩雀", 131: "皎月", 105: "小鱼 小鱼人",
    35: "小丑", 120: "人马", 427: "翠神", 203: "千珏",
    267: "娜美", 432: "巴德", 40: "迦娜 风女", 117: "璐璐 露露",
    412: "锤石", 25: "莫甘娜 堕落", 16: "索拉卡 奶妈 星妈",
    89: "日女 蕾欧娜", 53: "机器人 蒸汽", 555: "派克 水鬼",
    920: "小火龙 斯莫德", 711: "薇古丝 熬夜波比", 801: "梅尔",
    950: "纳亚菲利 狗", 233: "贝蕾亚 疯狗", 234: "佛爷 佛耶戈",
    21: "女枪 好运姐 赏金", 62: "猴子 齐天大圣", 67: "薇恩 VN",
    89: "日女", 117: "露露 仙灵女巫", 40: "风女",
    143: "婕拉 荆棘", 161: "大眼", 267: "娜美 人鱼",
    25: "莫甘娜", 98: "肾 慎", 154: "扎克 翔战士",
}


def _slug(name: str) -> str:
    """英雄英文名 -> 各网站使用的 URL slug。"""
    s = name.replace("Wukong", "MonkeyKing")  # 部分站点用 MonkeyKing
    s = re.sub(r"[^a-zA-Z'& .]", "", s)
    s = s.replace(" ", "").replace("'", "").replace(".", "").replace("&", "")
    return s.lower()


class ChampionDB:
    """英雄数据库：数字 ID、英文名、中文名、URL slug 互转。"""

    def __init__(self, lang: str = "zh_CN", timeout: float = 12.0):
        self.lang = lang
        self.timeout = timeout
        self.version: Optional[str] = None
        self.by_id: Dict[int, dict] = {}
        self.en_name_to_id: Dict[str, int] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        cache = CACHE_DIR / f"champions_{self.lang}.json"
        data = None

        # 缓存优先：本地已有英雄库就立即使用（毫秒级），
        # 版本更新交给后台 refresh_async() 静默完成，不阻塞启动。
        if cache.exists():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                self.version = cached.get("version")
                data = cached.get("data")
            except Exception:
                data = None

        # 仅当完全没有本地缓存（首次运行）时，才联网拉取一次。
        if not data:
            try:
                versions = requests.get(DDRAGON_VERSIONS, timeout=self.timeout).json()
                self.version = versions[0]
                url = DDRAGON_CHAMPIONS.format(version=self.version, lang=self.lang)
                data = requests.get(url, timeout=self.timeout).json()["data"]
                cache.write_text(
                    json.dumps({"version": self.version, "data": data},
                               ensure_ascii=False),
                    encoding="utf-8")
            except Exception:
                data = None

        self._fill(data)
        self._loaded = True

    def refresh_async(self) -> None:
        """后台静默检查英雄库是否有新版本；有则更新缓存，下次启动生效。

        英雄库是跨大版本才变动的静态数据，无需阻塞 UI。
        """
        import threading

        def _work():
            try:
                cache = CACHE_DIR / f"champions_{self.lang}.json"
                versions = requests.get(DDRAGON_VERSIONS, timeout=self.timeout).json()
                latest = versions[0]
                if latest == self.version:
                    return
                url = DDRAGON_CHAMPIONS.format(version=latest, lang=self.lang)
                new_data = requests.get(url, timeout=self.timeout).json()["data"]
                cache.write_text(
                    json.dumps({"version": latest, "data": new_data},
                               ensure_ascii=False),
                    encoding="utf-8")
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _fill(self, data) -> None:
        if data:
            for key, info in data.items():
                cid = int(info["key"])
                # Data Dragon zh_CN：name=称号（如"德玛西亚之力"），title=英雄名（如"盖伦"）
                cn_name = info.get("title") or info.get("name", key)   # 界面显示：盖伦
                cn_alias = info.get("name", "")                        # 搜索备用：称号
                self.by_id[cid] = {
                    "id": cid,
                    "en": key,
                    "name": cn_name,
                    "title": cn_name,
                    "alias": cn_alias if cn_alias != cn_name else "",
                    "tags": info.get("tags", []),
                    "slug": _slug(key),
                }
                self.en_name_to_id[key.lower().replace(" ", "").replace("'", "").replace(".", "")] = cid
        # 内置兜底，保证离线也能识别常见英雄
        for cid, en in FALLBACK_ID_TO_NAME.items():
            if cid not in self.by_id:
                self.by_id[cid] = {"id": cid, "en": en, "name": en, "title": en,
                                   "alias": "", "tags": [], "slug": _slug(en)}
        # 玩家常用俗称（社区叫法），纳入搜索与匹配
        for cid, alias in COMMON_ALIASES.items():
            if cid in self.by_id:
                old = self.by_id[cid].get("alias", "")
                merged = " ".join(dict.fromkeys([old, alias])) if old else alias
                self.by_id[cid]["alias"] = merged.strip()

    def display_name(self, cid: int) -> str:
        info = self.by_champion_id(cid)
        return info["name"] if info else f"英雄#{cid}"

    # ---- 查询方法 ----
    def by_champion_id(self, cid: int) -> Optional[dict]:
        self.load()
        return self.by_id.get(int(cid))

    def name_of(self, cid: int) -> str:
        info = self.by_champion_id(cid)
        return info["name"] if info else f"英雄#{cid}"

    def en_of(self, cid: int) -> str:
        info = self.by_champion_id(cid)
        return info["en"] if info else f"Champ{cid}"

    def slug_of_id(self, cid: int) -> str:
        info = self.by_champion_id(cid)
        return info["slug"] if info else str(cid)

    def id_by_en(self, en_name: str) -> Optional[int]:
        self.load()
        key = en_name.lower().replace(" ", "").replace("'", "").replace(".", "").replace("&", "")
        # 站点/旧名称差异
        aliases = {"monkeyking": "wukong", "wukong": "monkeyking",
                   "nunuwillump": "nunu", "belveth": "belveth"}
        return (self.en_name_to_id.get(key)
                or self.en_name_to_id.get(aliases.get(key, ""))
                or self._extra_alias(key))

    def _extra_alias(self, key: str) -> Optional[int]:
        """处理少数改名/多词英雄的模糊兜底。"""
        if key.startswith("nunu"):
            return self.en_name_to_id.get("nunu")
        if "renata" in key:
            for k, v in self.en_name_to_id.items():
                if k.startswith("renata"):
                    return v
        return None

    def all_ids(self) -> list[int]:
        self.load()
        return sorted(self.by_id.keys())
