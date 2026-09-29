"""英雄软属性表（人工策展 / curated）：Riot 官方不直接提供的 5 个"打法属性"。

官方能算的部分（定位 tags、近战远程 attackrange、血量/护甲/魔抗身板）不在这里，
由 champions.py 的官方 stats 自动给出。本表只补 Riot 没有的软属性：

  damage  伤害类型：AD 物理 / AP 魔法 / MIX 双伤 / TRUE 偏真伤
  style   伤害方式：BURST 瞬间爆发 / SUSTAIN 持续输出 / POKE 远程消耗
  mobility 位移能力：0 无位移 / 1 有自保位移 / 2 多段/长距离突进
  engage  开团能力：ENGAGE 强开 / PECK 反手保护 / NONE 无开团
  cc      控制强度：0 几乎无控 / 1 有软控或单体控 / 2 大量硬控/团控

数据为经验归纳（不是精确胜率），新英雄或重做时更新对应一行即可。
键为英雄英文名（去空格/点/& 的紧凑形式），与 ChampionDB.id_by_en 口径一致。
"""
from __future__ import annotations

from typing import Optional

# 取值常量（便于代码引用，避免散落的字符串）
AD, AP, MIX, TRUE = "AD", "AP", "MIX", "TRUE"
BURST, SUSTAIN, POKE = "BURST", "SUSTAIN", "POKE"
ENGAGE, PECK, NO_ENGAGE = "ENGAGE", "PECK", "NONE"

# 每条：cid: (damage, style, mobility, engage, cc)
RAW_TRAITS: dict[int, tuple] = {
    # ---- 刺客 / 进场型（高爆发 + 突进，是"对面刺客多"判断的主体）----
    238: (AD, BURST, 2, NO_ENGAGE, 1),     # Zed 劫
    91: (AD, BURST, 2, NO_ENGAGE, 1),      # Talon 男刀
    121: (AD, BURST, 2, NO_ENGAGE, 1),     # Khazix 卡兹克
    84: (AP, BURST, 2, NO_ENGAGE, 1),      # Akali 阿卡丽
    55: (AP, BURST, 2, NO_ENGAGE, 1),      # Katarina 卡特
    105: (AP, BURST, 2, NO_ENGAGE, 1),     # Fizz 小鱼人
    245: (AP, BURST, 2, NO_ENGAGE, 1),     # Ekko 艾克
    7: (AP, BURST, 2, NO_ENGAGE, 1),       # Leblanc 妖姬
    38: (MIX, BURST, 2, NO_ENGAGE, 1),     # Kassadin 卡萨丁
    28: (AP, BURST, 1, NO_ENGAGE, 1),      # Evelynn 寡妇
    107: (AD, BURST, 2, NO_ENGAGE, 1),     # Rengar 狮子狗
    56: (AD, BURST, 2, ENGAGE, 1),         # Nocturne 梦魇（大招强开）
    35: (AD, BURST, 1, NO_ENGAGE, 1),      # Shaco 小丑
    60: (MIX, BURST, 2, NO_ENGAGE, 2),     # Elise 蜘蛛
    76: (AP, BURST, 2, NO_ENGAGE, 1),      # Nidalee 豹女
    246: (AD, BURST, 2, NO_ENGAGE, 1),     # Qiyana 奇亚娜
    950: (AD, BURST, 2, NO_ENGAGE, 1),     # Naafiri 纳亚菲利
    805: (AP, BURST, 2, NO_ENGAGE, 1),     # Locke
    # ---- 战士 / 刺客混合型 ----
    11: (AD, SUSTAIN, 2, NO_ENGAGE, 1),    # MasterYi 剑圣
    23: (AD, SUSTAIN, 2, NO_ENGAGE, 1),    # Tryndamere 蛮王
    39: (MIX, SUSTAIN, 2, NO_ENGAGE, 1),   # Irelia 刀妹
    92: (AD, BURST, 2, ENGAGE, 1),         # Riven 锐雯
    131: (AP, SUSTAIN, 2, ENGAGE, 2),      # Diana 皎月
    157: (AD, SUSTAIN, 1, NO_ENGAGE, 1),   # Yasuo 亚索
    777: (MIX, BURST, 2, ENGAGE, 1),       # Yone 永恩
    164: (AD, BURST, 2, NO_ENGAGE, 2),     # Camille 卡蜜尔
    141: (AD, BURST, 2, NO_ENGAGE, 1),     # Kayn 凯隐
    254: (AD, BURST, 2, ENGAGE, 2),        # Vi 蔚
    234: (MIX, SUSTAIN, 1, NO_ENGAGE, 1),  # Viego 佛耶戈
    233: (AD, BURST, 2, NO_ENGAGE, 1),     # Briar 贝蕾亚
    517: (AP, BURST, 2, NO_ENGAGE, 1),     # Sylas 塞拉斯
    893: (AP, BURST, 2, NO_ENGAGE, 1),     # Aurora
    895: (AD, BURST, 2, ENGAGE, 1),        # Nilah 尼菈
    799: (AD, BURST, 2, NO_ENGAGE, 1),     # Ambessa
    200: (AD, SUSTAIN, 2, NO_ENGAGE, 1),   # Belveth 卑尔维斯
    887: (AP, SUSTAIN, 1, NO_ENGAGE, 1),   # Gwen 格温
    # ---- 纯坦克 / 开团前排 ----
    3: (AP, SUSTAIN, 1, ENGAGE, 2),        # Galio 加里奥（英雄登场强开+保护）
    14: (AD, SUSTAIN, 1, ENGAGE, 2),       # Sion 塞恩
    33: (AP, SUSTAIN, 1, ENGAGE, 2),       # Rammus 龙龟
    54: (AP, BURST, 1, ENGAGE, 2),         # Malphite 石头人
    32: (AP, SUSTAIN, 1, ENGAGE, 2),       # Amumu 阿木木
    31: (MIX, SUSTAIN, 1, ENGAGE, 2),      # Chogath 大虫子
    36: (AD, SUSTAIN, 1, ENGAGE, 1),       # DrMundo 蒙多
    27: (AP, SUSTAIN, 1, ENGAGE, 2),       # Singed 炼金
    20: (MIX, SUSTAIN, 1, ENGAGE, 2),      # Nunu 雪人
    98: (AP, SUSTAIN, 1, PECK, 2),         # Shen 慎（大招保护）
    516: (AD, SUSTAIN, 1, PECK, 1),        # Ornn 奥恩
    897: (AD, SUSTAIN, 2, PECK, 2),        # KSante 奎桑提
    526: (AP, SUSTAIN, 1, ENGAGE, 2),      # Rell 芮尔
    113: (AP, SUSTAIN, 2, ENGAGE, 2),      # Sejuani 猪妹
    154: (AP, SUSTAIN, 2, ENGAGE, 2),      # Zac 扎克
    72: (AD, SUSTAIN, 1, ENGAGE, 2),       # Skarner 蝎子
    78: (AD, BURST, 1, PECK, 2),           # Poppy 波比
    223: (AP, SUSTAIN, 1, PECK, 2),        # TahmKench 塔姆
    # ---- 重装战士 / 半肉 ----
    266: (AD, SUSTAIN, 1, ENGAGE, 1),      # Aatrox 剑魔
    122: (AD, BURST, 1, ENGAGE, 1),        # Darius 诺手
    58: (AD, BURST, 2, ENGAGE, 1),         # Renekton 鳄鱼
    86: (AD, SUSTAIN, 1, NO_ENGAGE, 1),    # Garen 盖伦
    19: (AD, SUSTAIN, 1, ENGAGE, 1),       # Warwick 狼人
    2: (AD, SUSTAIN, 2, ENGAGE, 1),        # Olaf 奥拉夫
    106: (AP, SUSTAIN, 1, ENGAGE, 1),      # Volibear 狗熊
    120: (AP, SUSTAIN, 2, ENGAGE, 2),      # Hecarim 人马
    48: (AD, SUSTAIN, 1, ENGAGE, 2),       # Trundle 巨魔
    75: (AD, SUSTAIN, 1, NO_ENGAGE, 1),    # Nasus 狗头
    82: (AP, SUSTAIN, 1, NO_ENGAGE, 2),    # Mordekaiser 铁男
    420: (AD, SUSTAIN, 1, NO_ENGAGE, 0),   # Illaoi 俄洛伊
    875: (AD, BURST, 1, ENGAGE, 1),        # Sett 瑟提
    83: (AD, SUSTAIN, 1, NO_ENGAGE, 1),    # Yorick 约里克
    80: (AD, BURST, 2, ENGAGE, 2),         # Pantheon 潘森
    59: (AD, BURST, 2, ENGAGE, 2),         # JarvanIV 皇子
    62: (AD, BURST, 2, ENGAGE, 2),         # MonkeyKing 猴子
    240: (AD, SUSTAIN, 1, ENGAGE, 2),      # Kled 克烈
    421: (AD, BURST, 2, ENGAGE, 2),        # RekSai 雷克塞
    150: (AD, SUSTAIN, 2, ENGAGE, 2),      # Gnar 纳尔
    77: (MIX, SUSTAIN, 2, ENGAGE, 2),      # Udyr 乌迪尔
    6: (AD, SUSTAIN, 1, ENGAGE, 2),        # Urgot 厄加特
    79: (AP, BURST, 2, ENGAGE, 2),         # Gragas 酒桶
    68: (AP, SUSTAIN, 1, ENGAGE, 2),       # Rumble 兰博
    904: (AD, SUSTAIN, 1, ENGAGE, 1),      # Zaahen
    102: (AP, SUSTAIN, 2, NO_ENGAGE, 0),   # Shyvana 龙女
    876: (AP, SUSTAIN, 2, NO_ENGAGE, 1),   # Lillia 莉莉娅
    114: (AD, SUSTAIN, 2, NO_ENGAGE, 1),   # Fiora 剑姬
    41: (AD, SUSTAIN, 1, NO_ENGAGE, 1),    # Gangplank 船长
    126: (AD, BURST, 1, NO_ENGAGE, 1),     # Jayce 杰斯
    # ---- 法师：爆发 ----
    1: (AP, BURST, 0, NO_ENGAGE, 1),       # Annie 安妮
    134: (AP, BURST, 0, NO_ENGAGE, 1),     # Syndra 辛德拉
    45: (AP, BURST, 0, NO_ENGAGE, 1),      # Veigar 小法
    711: (AP, BURST, 0, NO_ENGAGE, 1),     # Vex 薇古丝
    127: (AP, BURST, 1, ENGAGE, 2),        # Lissandra 冰女
    103: (AP, BURST, 2, NO_ENGAGE, 1),     # Ahri 狐狸
    8: (AP, SUSTAIN, 1, NO_ENGAGE, 0),     # Vladimir 吸血鬼
    # ---- 法师：控制 / 团控 ----
    9: (AP, SUSTAIN, 0, ENGAGE, 2),        # Fiddlesticks 稻草人
    34: (AP, SUSTAIN, 0, PECK, 2),         # Anivia 冰鸟
    69: (AP, SUSTAIN, 1, NO_ENGAGE, 2),    # Cassiopeia 蛇女
    90: (AP, SUSTAIN, 0, NO_ENGAGE, 2),    # Malzahar 蚂蚱
    50: (AP, SUSTAIN, 1, ENGAGE, 2),       # Swain 斯维因
    518: (AP, BURST, 1, ENGAGE, 2),        # Neeko 妮蔻
    13: (AP, SUSTAIN, 0, NO_ENGAGE, 1),    # Ryze 瑞兹
    30: (AP, SUSTAIN, 0, NO_ENGAGE, 1),    # Karthus 死歌
    112: (AP, SUSTAIN, 0, NO_ENGAGE, 1),   # Viktor 三只手
    268: (AP, SUSTAIN, 1, NO_ENGAGE, 1),   # Azir 沙皇
    136: (AP, SUSTAIN, 1, NO_ENGAGE, 1),   # AurelionSol 龙王
    142: (AP, BURST, 2, NO_ENGAGE, 1),     # Zoe 佐伊
    910: (AP, BURST, 0, NO_ENGAGE, 2),     # Hwei
    800: (AP, BURST, 1, NO_ENGAGE, 1),     # Mel 梅尔
    801: (AP, BURST, 1, NO_ENGAGE, 1),     # Mel（新ID别名）
    # ---- 法师：消耗 / Poke ----
    161: (AP, POKE, 0, NO_ENGAGE, 1),      # Velkoz 大眼
    115: (AP, POKE, 1, NO_ENGAGE, 1),      # Ziggs 炸弹人
    101: (AP, POKE, 0, NO_ENGAGE, 1),      # Xerath 泽拉斯
    85: (AP, POKE, 1, ENGAGE, 2),          # Kennen 凯南（大招团控）
    74: (AP, POKE, 0, NO_ENGAGE, 2),       # Heimerdinger 大头
    163: (AP, BURST, 1, ENGAGE, 2),        # Taliyah 岩雀
    4: (MIX, POKE, 1, NO_ENGAGE, 1),       # TwistedFate 卡牌
    # ---- 射手 Marksman ----
    22: (AD, SUSTAIN, 0, NO_ENGAGE, 1),    # Ashe 艾希（大招开团，但身板脆，归 PECK）
    51: (AD, SUSTAIN, 0, NO_ENGAGE, 1),    # Caitlyn 女警
    222: (AD, SUSTAIN, 0, NO_ENGAGE, 0),   # Jinx 金克丝
    81: (MIX, POKE, 2, NO_ENGAGE, 0),      # Ezreal EZ
    236: (AD, BURST, 1, NO_ENGAGE, 0),     # Lucian 卢锡安
    145: (AD, SUSTAIN, 2, NO_ENGAGE, 0),   # Kaisa 卡莎
    119: (AD, BURST, 1, NO_ENGAGE, 0),     # Draven 德莱文
    15: (AD, SUSTAIN, 0, NO_ENGAGE, 0),    # Sivir 希维尔
    29: (AD, SUSTAIN, 1, NO_ENGAGE, 0),    # Twitch 老鼠
    67: (AD, SUSTAIN, 1, NO_ENGAGE, 1),    # Vayne VN
    96: (MIX, SUSTAIN, 0, NO_ENGAGE, 0),   # KogMaw 大嘴
    21: (AD, BURST, 0, NO_ENGAGE, 0),      # MissFortune 女枪
    18: (AD, BURST, 2, NO_ENGAGE, 0),      # Tristana 小炮
    17: (AP, POKE, 0, NO_ENGAGE, 1),       # Teemo 提莫
    202: (AD, BURST, 0, NO_ENGAGE, 1),     # Jhin 烬
    110: (MIX, POKE, 0, NO_ENGAGE, 1),     # Varus 韦鲁斯
    104: (AD, BURST, 1, NO_ENGAGE, 1),     # Graves 男枪
    360: (AD, BURST, 1, ENGAGE, 0),       # Samira 莎弥拉
    221: (AD, SUSTAIN, 1, NO_ENGAGE, 0),   # Zeri 泽丽
    498: (AD, SUSTAIN, 1, NO_ENGAGE, 0),   # Xayah 霞
    523: (AD, SUSTAIN, 0, NO_ENGAGE, 0),   # Aphelios 厄斐琉斯
    429: (AD, SUSTAIN, 1, NO_ENGAGE, 0),   # Kalista 卡莉斯塔
    203: (AD, SUSTAIN, 2, NO_ENGAGE, 0),   # Kindred 千珏
    235: (AD, SUSTAIN, 1, NO_ENGAGE, 1),   # Senna 赛娜
    166: (AD, SUSTAIN, 2, NO_ENGAGE, 1),   # Akshan 阿克尚
    133: (AD, SUSTAIN, 1, NO_ENGAGE, 0),   # Quinn 奎因
    42: (MIX, SUSTAIN, 1, NO_ENGAGE, 0),   # Corki 飞机
    901: (AP, POKE, 1, NO_ENGAGE, 0),      # Smolder 斯莫德
    920: (AP, POKE, 1, NO_ENGAGE, 0),      # Smolder（新ID别名）
    804: (AD, SUSTAIN, 2, NO_ENGAGE, 0),   # Yunara
    10: (AP, SUSTAIN, 2, NO_ENGAGE, 1),    # Kayle 天使
    # ---- 辅助：开团 / 坦克型 ----
    12: (AP, SUSTAIN, 1, ENGAGE, 2),       # Alistar 牛头
    53: (AP, SUSTAIN, 1, ENGAGE, 2),       # Blitzcrank 机器人
    89: (AP, SUSTAIN, 1, ENGAGE, 2),       # Leona 日女
    111: (AP, SUSTAIN, 1, ENGAGE, 2),      # Nautilus 泰坦
    412: (AP, SUSTAIN, 1, ENGAGE, 2),      # Thresh 锤石
    555: (AD, BURST, 2, ENGAGE, 2),        # Pyke 派克
    497: (AP, SUSTAIN, 2, ENGAGE, 2),      # Rakan 洛
    44: (AP, SUSTAIN, 1, PECK, 2),         # Taric 宝石
    201: (AP, SUSTAIN, 1, PECK, 2),        # Braum 布隆
    # ---- 辅助：保护 / 消耗型（软辅）----
    40: (AP, POKE, 1, PECK, 1),            # Janna 风女
    16: (AP, SUSTAIN, 0, PECK, 1),         # Soraka 奶妈
    117: (AP, POKE, 1, PECK, 1),           # Lulu 璐璐
    25: (AP, POKE, 0, PECK, 2),            # Morgana 莫甘娜
    267: (AP, SUSTAIN, 1, PECK, 1),        # Nami 娜美
    37: (AP, SUSTAIN, 0, PECK, 1),         # Sona 琴女
    26: (AP, BURST, 1, PECK, 1),           # Zilean 时光
    43: (AP, POKE, 1, PECK, 1),            # Karma 卡尔玛
    147: (AP, SUSTAIN, 0, PECK, 1),        # Seraphine 萨勒芬妮
    350: (AP, SUSTAIN, 0, PECK, 0),        # Yuumi 猫咪
    63: (AP, BURST, 0, NO_ENGAGE, 2),      # Brand 火男
    143: (AP, POKE, 0, NO_ENGAGE, 2),      # Zyra 婕拉
    902: (AP, POKE, 1, PECK, 1),           # Milio 米利欧
    888: (AP, POKE, 1, PECK, 2),           # Renata 烈娜塔
    432: (AP, POKE, 1, PECK, 2),           # Bard 巴德
    427: (AP, SUSTAIN, 1, PECK, 2),        # Ivern 翠神
    5: ('AD', 'BURST', 2, 'ENGAGE', 2),
    24: ('AD', 'SUSTAIN', 1, 'ENGAGE', 1),
    57: ('AP', 'SUSTAIN', 1, 'ENGAGE', 2),
    61: ('AP', 'BURST', 0, 'PECK', 2),
    64: ('AD', 'BURST', 2, 'ENGAGE', 1),
    99: ('AP', 'BURST', 0, 'PECK', 1),
}


def _default_traits(cid: int) -> tuple:
    """对未收录英雄给出保守默认值（不假装确定，位移/控制按 1 处理）。"""
    return (AP, SUSTAIN, 1, PECK, 1)


def traits_of(cid: int) -> tuple:
    """按英雄 ID 返回 5 个软属性 (damage, style, mobility, engage, cc)。"""
    return RAW_TRAITS.get(int(cid)) or _default_traits(int(cid))


def damage_type(cid: int) -> str:
    """返回伤害类型 AD/AP/MIX/TRUE。"""
    return traits_of(cid)[0]


def is_assassin_burst(cid: int) -> bool:
    """是否属于'高爆发突进'威胁（刺客 + 爆发型进场战士），用于'对面刺客多'。"""
    d, style, mob, eng, cc = traits_of(cid)
    return style == BURST and mob >= 2


def has_escape(cid: int) -> bool:
    """是否有位移自保能力（mobility>=1）。"""
    return traits_of(cid)[2] >= 1


def is_hard_engage(cid: int) -> bool:
    """是否为强开团英雄。"""
    return traits_of(cid)[3] == ENGAGE
