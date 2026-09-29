"""配置管理：所有可调项集中在这里，也支持 config.json 覆盖。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

# ---- 默认英雄联盟客户端安装路径（国服 WeGame / WeGameApps / 官服常见位置）----
# 注：找不到时还会动态扫描全部盘符 + 进程定位（见 lcu.py），这里只是快路径。
DEFAULT_CLIENT_GLOBS = [
    r"C:\WeGameApps\英雄联盟\LeagueClient",
    r"D:\WeGameApps\英雄联盟\LeagueClient",
    r"E:\WeGameApps\英雄联盟\LeagueClient",
    r"F:\WeGameApps\英雄联盟\LeagueClient",
    r"G:\WeGameApps\英雄联盟\LeagueClient",
    r"I:\WeGameApps\英雄联盟\LeagueClient",
    r"C:\WeGame\英雄联盟\LeagueClient",
    r"C:\Program Files\WeGame\英雄联盟\LeagueClient",
    r"D:\WeGame\英雄联盟\LeagueClient",
    r"D:\英雄联盟\LeagueClient",
    r"E:\WeGame\英雄联盟\LeagueClient",
    r"C:\Riot Games\League of Legends\LeagueClient",
    r"D:\Riot Games\League of Legends\LeagueClient",
    r"C:\Program Files\Riot Games\League of Legends\LeagueClient",
]

CONFIG_DIR = Path.home() / ".lol_counter_picker"
CONFIG_PATH = CONFIG_DIR / "config.json"
CACHE_DIR = CONFIG_DIR / "cache"


@dataclass
class Config:
    # 英雄联盟客户端 LeagueClient 文件夹路径（留空则自动查找 lockfile）
    client_path: str = ""
    # 数据数据源优先级，按顺序尝试：lolalytics / blitz / offline
    data_providers: list[str] = field(default_factory=lambda: ["lolalytics", "blitz", "offline"])
    # 段位筛选（lolalytics 页面默认 emerald_plus；国内访问失败会自动降级）
    tier: str = "emerald_plus"
    # 推荐列表显示数量
    top_n: int = 8
    # 轮询间隔（秒）
    poll_interval: float = 2.0
    # 对位胜率低于多少（敌方视角）才算推荐的 counter；数值越低越克制
    counter_winrate_threshold: float = 49.5
    # 网络请求超时（秒）
    http_timeout: float = 12.0
    # 是否显示敌方全部五个位置（进游戏后）
    show_all_enemy_lanes: bool = True
    # UI 语言
    language: str = "zh_CN"

    @classmethod
    def load(cls) -> "Config":
        """读取本地配置文件；文件不存在或损坏时返回一份默认配置。"""
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            except Exception:
                pass
        return cfg

    def save(self) -> None:
        """把当前配置写回本地 JSON 文件。"""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )


CACHE_DIR.mkdir(parents=True, exist_ok=True)
