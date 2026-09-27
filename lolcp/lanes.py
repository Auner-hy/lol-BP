"""英雄分路归类：把推荐英雄过滤到玩家所在分路。

统计站点的克制列表会混入其他路英雄（上路对位里出现中单英雄等）。
用英雄英文 ID -> 主要分路的映射过滤；映射覆盖全英雄，未知英雄退回
Data Dragon 的 tags 粗略判断，宁可不误杀（过滤宽松一点）。
"""
from __future__ import annotations

from typing import Set

# 英雄英文 ID -> 常见分路集合
CHAMPION_LANES: dict[str, Set[str]] = {
    # ---- 上单 ----
    "Aatrox": {"top"}, "Camille": {"top"}, "Chogath": {"top"}, "Darius": {"top"},
    "DrMundo": {"top"}, "Fiora": {"top"}, "Gangplank": {"top"}, "Garen": {"top"},
    "Gnar": {"top"}, "Gragas": {"top", "jungle"}, "Gwen": {"top"}, "Heimerdinger": {"top", "middle"},
    "Illaoi": {"top"}, "Irelia": {"top", "middle"}, "Jax": {"top", "jungle"},
    "Jayce": {"top", "middle"}, "KSante": {"top"}, "Kayle": {"top"},
    "Kennen": {"top"}, "Kled": {"top"}, "Lillia": {"top", "jungle"},
    "Malphite": {"top", "support"}, "Mordekaiser": {"top"}, "Nasus": {"top"},
    "Olaf": {"top", "jungle"}, "Ornn": {"top"}, "Poppy": {"top", "jungle"},
    "Quinn": {"top"}, "Renekton": {"top"}, "Riven": {"top"}, "Rumble": {"top", "middle"},
    "Sett": {"top", "support"}, "Shen": {"top"}, "Singed": {"top"},
    "Sion": {"top"}, "Teemo": {"top"}, "Trundle": {"top", "jungle"},
    "Tryndamere": {"top"}, "Urgot": {"top"}, "Volibear": {"top", "jungle"},
    "Yorick": {"top"}, "Yasuo": {"top", "middle", "bottom"}, "Yone": {"top", "middle"},
    "Ambessa": {"top"}, "Smolder": {"middle", "bottom"},
    # ---- 打野 ----
    "Belveth": {"jungle"}, "Briar": {"jungle"}, "Diana": {"jungle", "middle"},
    "Ekko": {"jungle", "middle"}, "Elise": {"jungle"}, "Evelynn": {"jungle"},
    "Graves": {"jungle"}, "Hecarim": {"jungle"}, "Ivern": {"jungle"},
    "JarvanIV": {"jungle"}, "Karthus": {"jungle"}, "Kayn": {"jungle"},
    "KhaZix": {"jungle"}, "Kindred": {"jungle"}, "LeeSin": {"jungle"},
    "MasterYi": {"jungle"}, "Nidalee": {"jungle"}, "Nunu": {"jungle"},
    "Nocturne": {"jungle"}, "Rammus": {"jungle"}, "RekSai": {"jungle"},
    "Rengar": {"jungle"}, "Sejuani": {"jungle"}, "Shaco": {"jungle", "support"},
    "Shyvana": {"jungle", "top"}, "Skarner": {"jungle"}, "Udyr": {"jungle"},
    "Viego": {"jungle"}, "Vi": {"jungle"}, "Warwick": {"jungle"},
    "Zac": {"jungle"}, "Naafiri": {"jungle", "middle"},
    # ---- 中单 ----
    "Ahri": {"middle"}, "Akali": {"middle", "top"}, "Anivia": {"middle"},
    "Annie": {"middle", "support"}, "AurelionSol": {"middle"}, "Azir": {"middle"},
    "Brand": {"middle", "support"}, "Cassiopeia": {"middle"}, "Corki": {"middle"},
    "Fizz": {"middle"}, "Galio": {"middle"}, "Hwei": {"middle"},
    "Kassadin": {"middle"}, "Katarina": {"middle"}, "LeBlanc": {"middle"},
    "Lissandra": {"middle"}, "Lux": {"middle", "support"}, "Malzahar": {"middle"},
    "Mel": {"middle"}, "Neeko": {"middle", "support"}, "Orianna": {"middle"},
    "Qiyana": {"middle"}, "Ryze": {"middle"}, "Swain": {"middle", "bottom", "support"},
    "Sylas": {"middle"}, "Syndra": {"middle"}, "Talon": {"middle", "jungle"},
    "Tristana": {"middle", "bottom"}, "TwistedFate": {"middle"},
    "Veigar": {"middle", "support"}, "Velkoz": {"middle", "support"},
    "Vex": {"middle"}, "Viktor": {"middle"}, "Vladimir": {"middle", "top"},
    "Xerath": {"middle", "support"}, "Zed": {"middle"}, "Ziggs": {"middle", "bottom"},
    "Zoe": {"middle"}, "Zyra": {"middle", "support"}, "Akshan": {"middle", "top"},
    "Taliyah": {"middle", "jungle"}, "Pantheon": {"middle", "top", "support"},
    "Twitch": {"bottom", "jungle"}, "Zeri": {"bottom"},
    # ---- 下路 ----
    "Aphelios": {"bottom"}, "Ashe": {"bottom", "support"}, "Caitlyn": {"bottom"},
    "Draven": {"bottom"}, "Ezreal": {"bottom"}, "Jhin": {"bottom"},
    "Jinx": {"bottom"}, "Kaisa": {"bottom"}, "Kalista": {"bottom"},
    "KogMaw": {"bottom"}, "Lucian": {"bottom", "middle"}, "MissFortune": {"bottom"},
    "Nilah": {"bottom"}, "Samira": {"bottom"}, "Senna": {"bottom", "support"},
    "Seraphine": {"bottom", "support"}, "Sivir": {"bottom"}, "Varus": {"bottom"},
    "Vayne": {"bottom", "top"}, "Xayah": {"bottom"}, "Yunara": {"bottom"},
    # ---- 辅助 ----
    "Alistar": {"support"}, "Bard": {"support"}, "Blitzcrank": {"support"},
    "Braum": {"support"}, "Janna": {"support"}, "Karma": {"support"},
    "Leona": {"support"}, "Lulu": {"support"}, "Milio": {"support"},
    "Morgana": {"support"}, "Nami": {"support"}, "Nautilus": {"support"},
    "Pyke": {"support"}, "Rakan": {"support"}, "Rell": {"support"},
    "Renata": {"support"}, "Soraka": {"support"}, "TahmKench": {"support", "top"},
    "Thresh": {"support"}, "Yuumi": {"support"}, "Zilean": {"support", "middle"},
    # ---- 特殊/多位置 ----
    "Fiddlesticks": {"jungle", "support"}, "Maokai": {"support", "top", "jungle"},
    "MonkeyKing": {"jungle", "top"}, "Wukong": {"jungle", "top"},
    "NunuWillump": {"jungle"}, "DrMundo ": {"top"},
    "Aurora": {"middle"},
}


def _normalize(en: str) -> str:
    s = en.replace(" ", "").replace("'", "").replace(".", "").replace("&", "")
    s = s.replace("Wukong", "MonkeyKing")
    if s == "BelVeth":
        s = "Belveth"
    if s == "KogMaw":
        s = "KogMaw"
    if s == "RenataGlasc":
        s = "Renata"
    if s == "NunuWillump":
        s = "Nunu"
    if s == "DrMundo":
        s = "DrMundo"
    return s


def lanes_of(en_name: str) -> Set[str]:
    """返回英雄可能出现的分路集合；未知英雄返回空集（不做过滤）。"""
    key = _normalize(en_name)
    if key in CHAMPION_LANES:
        return CHAMPION_LANES[key]
    return set()


def belongs_to_lane(en_name: str, lane: str) -> bool:
    """英雄是否属于指定分路。未知英雄返回 True（宽松，不误杀）。"""
    if not lane:
        return True
    lanes = lanes_of(en_name)
    if not lanes:
        return True
    return lane in lanes
