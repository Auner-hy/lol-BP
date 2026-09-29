# LOL 对位识别 & Counter 英雄推荐工具

**当前版本：v1.1.0** ｜ 完整更新记录见 [CHANGELOG.md](CHANGELOG.md)

在英雄联盟对局过程中，自动识别对位（同路）敌方英雄，并根据全网对位胜率数据
推荐克制英雄。**原生桌面窗口**（深色海克斯风格、英雄头像、胜率条）实时刷新，
选将阶段和游戏内都能用。

## 窗口界面一览

- **状态栏**：状态指示灯（灰=未检测到客户端 / 金=在线大厅 / 青=选将BP / 绿=游戏中）；
- **对位目标卡片**：大头像 + 英雄名 + 自动/手动标记 + 查询分路；
- **敌方阵容条**：五个敌方头像 + 分路标签，**点击任意头像即可切换**查询目标；
- **克制推荐列表**：按 Lolalytics 归一化克制幅度排序（官方 Counter 口径，
  已剔除英雄强度差异），前三名金/银/铜名次、彩色胜率条
  （绿=明显克制、青=小优、金=均势、灰=劣势），胜率为我方对位胜率，可滚动；
- **手动查询**：`🔍 手动查询` 弹窗支持中英文名模糊搜索 + 分路选择，
  进游戏前想提前查任意英雄都可以；`↩ 恢复自动` 回到自动识别；
- **窗口置顶**：可开关，默认置顶方便 BP 时边选边看；
- **高 DPI 清晰渲染**：自动适配 Windows 高分屏缩放（125%/150%/200%），
  文字、头像、间距按系统缩放比整体放大，不再发虚；
- 英雄头像使用 128px 高清源（Community Dragon）+ Pillow 无损缩放，
  首次出现时自动下载缓存。

## 响应速度

- 进入识别后**内置离线克制表立即出结果（0 等待）**，界面不卡顿；
- 在线统计源（Lolalytics / Blitz）**后台并行竞速**拉取，拿到后自动
  替换为在线胜率数据（通常 2–4 秒，底部数据源标记会从"离线表"变为"在线数据"）；
- 轮询间隔 2 秒，状态变化快速响应；网络再差也能秒出离线结果。

## 工作原理（全部只读，安全无注入）

| 阶段 | 识别方式 | 数据通道 |
|------|----------|----------|
| BP / 选将 | 读取客户端本地 LCU 接口 | `https://127.0.0.1:<端口>/lol-champ-select/v1/session` |
| 游戏进行中 | Riot 官方 Live Client Data API | `https://127.0.0.1:2999/liveclientdata/allgamedata`（[官方文档](https://developer.riotgames.com/docs/lol)） |

**LCU 凭证自动获取（v7，四级降级，国服/官服均适用）：**
1. 安装目录下 `lockfile`（含端口和随机密码，官服/外服标准方式）；
2. **客户端 UX 启动日志**（国服部分环境 lockfile 损坏为 0 字节时的主通道：
   `LeagueClientUx.log` 中记录了本次会话的 `--remoting-auth-token` 和
   `--app-port`，普通文本文件、用户权限可读，不依赖进程信息）；
3. 端口与 `netstat` 中 LeagueClient.exe 实际监听端口交叉验证；
4. 进程定位 + 盘符浅扫描作为最后兜底。

每组凭证都会向客户端发起一次只读请求实测，验证通过才使用。

这两个通道与 OP.GG、WeGame 英雄联盟助手、Blitz 等工具所用的原理相同：
- **只发 GET 只读请求**，不修改客户端/游戏任何状态；
- **不读取游戏进程内存、不注入 DLL、不模拟键鼠、不抓包改包**；
- 数据只在本机 `127.0.0.1` 回环内传输，不接触账号密码；
- 不依赖 Vanguard 敏感权限。即便如此，使用第三方工具始终存在理论风险，
  请自行评估（Riot 官方明确允许读取 Live Client Data API）。

### 对位数据来源（自动降级）

1. **Lolalytics**（默认）— 解析其对位页面，数据按翡翠+段位、当前版本统计；
2. **Blitz.gg** — 备用公开统计源；
3. **内置离线克制表** — 完全断网时兜底，覆盖五个位置 140+ 常见英雄。

> 说明：WeGame / 101.qq.com 的"对位克制"数据**没有公开 API**，只集成在
> WeGame 自家客户端内（其数据由腾讯私有统计产生）。本工具使用公开统计源
> 替代，胜率口径与 WeGame 接近；国内网络无法访问 Lolalytics 时会自动降级
> 到离线表。数据缓存 6 小时，保存在 `~/.lol_counter_picker/cache/`。

## 快速开始（Windows）

1. 安装 **Python 3.9 或更高版本**：<https://www.python.org/downloads/>
   安装时务必勾选 **「Add Python to PATH」**。
2. 双击 **`run.bat`**（首次运行会自动安装 `requests` 依赖）。
3. 正常打开英雄联盟、排队进游戏即可：
   - 进入 **BP/选将界面** 后，窗口自动显示我方英雄、敌方已锁英雄和对位目标；
   - 敌方阵容以头像排列，**点击任意敌方头像**即可切换查询目标；
   - 也可以在窗口**顶部搜索框**输入任意英雄（支持中文名/英文名/俗称，回车）、
     选择分路提前查克制；
   - 游戏开始后自动刷新，显示五名敌方英雄；推荐列表带胜率条、可滚动。

## 免安装 exe（发给没有 Python 的朋友）

在**自己装了 Python 的电脑**上双击 **`build_exe.bat`**，约 1–3 分钟后：

- 生成单文件 `dist\LOLCounterPicker.exe`（建议先双击自测）；
- 自动打包出 `LOL-Counter-Picker-EXE.zip`，**把这个 zip 发给朋友即可**——
  对方解压后双击 exe 就能用，无需安装 Python；
- 首次运行 Windows SmartScreen / 杀毒可能对未签名 exe 弹一次提示，
  选「更多信息」→「仍要运行」即可（PyInstaller 打包程序的常见现象）。

> 注意：exe 必须在 **Windows 上** 构建（PyInstaller 不支持跨平台打包）。

## 命令行用法（调试用）

```bash
pip install -r requirements.txt

python -m lolcp                         # 启动桌面窗口（默认）
python -m lolcp --cli                   # 命令行模式（无 GUI）
python -m lolcp --test-data             # 离线自测，不需要游戏
python -m lolcp --enemy Darius --lane top   # 直接查"诺手 上单"的克制英雄
```

## 配置（可选）

配置文件：`~/.lol_counter_picker/config.json`（首次运行后生成）

```json
{
  "client_path": "",                 // 手动指定 LeagueClient 文件夹（留空自动查找）
  "data_providers": ["lolalytics", "blitz", "offline"],
  "tier": "emerald_plus",            // 段位口径
  "top_n": 8,                        // 推荐数量
  "poll_interval": 3.0,              // 刷新间隔（秒）
  "counter_winrate_threshold": 49.5
}
```

自动查找客户端（**WeGame 国服与 Riot 官服都认**，与启动方式无关）：
1. 内置常见路径表：`C:\WeGame\英雄联盟\LeagueClient`、
   `D:\WeGame\英雄联盟\LeagueClient`、`X:\英雄联盟\LeagueClient`、
   Riot 官服 `Riot Games\League of Legends\LeagueClient` 等；
2. 进程定位：通过运行中的 `LeagueClient.exe` 反查安装目录；
3. 盘符浅扫描：`X:\WeGame*\英雄联盟\LeagueClient` 等通配路径；
4. 游戏内识别走 Live Client Data API（`127.0.0.1:2999`），**不需要
   知道安装路径**，任何方式启动都生效。
以上都找不到时，可在配置里手动填 `client_path`（LeagueClient 文件夹路径）。

## 项目结构

```
lol_counter_picker/
├── run.bat                 # Windows 一键启动（源码方式，需 Python）
├── build_exe.bat           # 一键打包免安装 exe（开发者/分发者用）
├── run_app.py              # exe 打包入口
├── requirements.txt
├── README.md
└── lolcp/
    ├── __main__.py         # 入口（GUI / CLI / 自测）
    ├── config.py           # 配置管理
    ├── champions.py        # 英雄 ID/中文名映射（Data Dragon 官方数据）
    ├── lcu.py              # LCU 客户端接口（选将阶段识别）
    ├── live_api.py         # Riot 官方 Live Client Data API（游戏内识别）
    ├── matchup.py          # 对位数据源抓取 + 推荐引擎
    ├── lanes.py            # 英雄分路归类（过滤跨路推荐）
    ├── offline_data.py     # 离线克制表（断网兜底）
    ├── engine.py           # 状态机：轮询客户端、产出对位快照
    └── app.py              # Tkinter 原生桌面窗口（深色主题/头像/胜率条/手动查询）
```

## 已知限制

- **排位/征召模式**：位置已分配，自动定位同路敌人，体验最完整；
- **盲选模式**：敌方锁定前看不到英雄（游戏机制限制），锁定后可点击切换查看；
- 大乱斗等无分路模式会按"全体敌人"展示，推荐分路判断可能不准；
- 统计站点偶尔改版可能导致解析失效，离线表始终可用。

## 免责声明

本工具仅做本地只读数据访问，不修改游戏、不提供对战内不公平优势。
请遵守《英雄联盟》用户协议；使用第三方工具的风险由使用者自行承担。
对位胜率数据归 Lolalytics / Blitz 各自所有，英雄数据来自 Riot 官方 Data Dragon。
