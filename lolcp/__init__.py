"""LoL Counter Picker (lolcp)

英雄联盟对位识别与 Counter 推荐工具。
仅做只读访问：LCU 本地客户端接口 + Riot 官方 Live Client Data API。
不读取/写入游戏内存，不注入进程，不模拟键鼠操作。
"""


def _setup_system_trust() -> None:
    """让 Python 直接信任操作系统证书库。

    某些本机网络加速 / 杀毒 / 代理软件（例如 SteamTools）会用自签根证书
    对 HTTPS 流量做中间人（MITM）解密。该根证书通常已经装进 Windows
    证书库（浏览器认），但不在 Python 自带的 certifi 包里，于是 requests
    会报 SSLCertVerificationError，导致在线胜率和更新检测 / 下载失败。

    truststore（谷歌维护的库）把 Python 的 ssl 验证切换为直接调用
    操作系统证书库，行为与浏览器保持一致；未安装该库时静默跳过。

    必须在任何模块 import requests 之前调用 —— 本包 __init__ 会先于
    所有 lolcp 子模块执行，所以放在这里最早。
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        # 环境里没有 truststore 时退回 Python 默认（certifi）行为。
        pass


_setup_system_trust()

__version__ = "1.4.3-beta"
