"""原生桌面窗口客户端（Tkinter，Python 自带，零强制 GUI 依赖）。

高 DPI 适配：启动时声明 Windows DPI 感知，字体/头像/间距按系统缩放比
整体放大，高分屏下文字与头像都清晰；头像使用 128px 高清源 + Pillow
Lanczos 缩放（无 Pillow 时自动降级）。

响应速度：推荐结果"离线表秒出首帧 + 在线源后台并行竞速"，在线数据
（lolalytics/blitz）拉到后自动替换，不再卡住轮询与界面。
"""
from __future__ import annotations

import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from urllib.parse import quote

import requests

from .config import Config, CACHE_DIR
from .engine import Engine, LANE_CN

try:
    from PIL import Image, ImageTk  # 高清缩放（Pillow），缺失时自动降级
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

# ---------- 海克斯主题色 ----------
BG       = "#0B1220"
CARD     = "#131C30"
CARD2    = "#18233B"
BORDER   = "#26314D"
GOLD     = "#C8AA6E"
GOLD_DIM = "#8C7B53"
TEAL     = "#0AC8B9"
RED      = "#E84057"
GREEN    = "#3DDC97"
TEXT     = "#E8E6E3"
MUTED    = "#7A8299"
FONT     = "Microsoft YaHei UI"

# 段位下拉：(显示名, lolalytics tier参数)，全部实测有效
TIER_OPTS = [
    ("黄金及以上", "gold_plus"),
    ("白金及以上", "platinum_plus"),
    ("翡翠及以上", "emerald_plus"),
    ("钻石及以上", "diamond_plus"),
    ("大师及以上", "master_plus"),
]

AVATAR_DIR = CACHE_DIR / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
# 符文/装备/技能等小图标缓存（按 URL 哈希命名）
ICON_DIR = CACHE_DIR / "icons"
ICON_DIR.mkdir(parents=True, exist_ok=True)

# ---------- 高 DPI 全局缩放 ----------
SCALE = 1.0


def _init_dpi() -> float:
    """在创建窗口前声明 DPI 感知，返回系统缩放比（1.0 / 1.25 / 1.5 / 2.0…）。"""
    scale = 1.0
    try:
        # PER_MONITOR_AWARE_V2 = -4（Win10 1703+），失败则逐级降级
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 逐显示器感知
            except Exception:
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(1)
                except Exception:
                    ctypes.windll.user32.SetProcessDPIAware()
        try:
            scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
        except Exception:
            scale = 1.0
    except Exception:
        scale = 1.0
    return max(1.0, scale)


def sp(v: float) -> int:
    """逻辑像素 -> 物理像素。"""
    return int(round(v * SCALE))


def fnt(size: float, weight: str = "normal") -> tuple:
    return (FONT, max(8, int(round(size * SCALE))), weight)


PHASE_DOT = {
    "no_client":    ("#5A6378", "未检测到客户端"),
    "lobby":        (GOLD,      "客户端在线 · 等待对局"),
    "champ_select": (TEAL,      "选将阶段（BP）"),
    "in_game":      (GREEN,     "游戏进行中"),
    "error":        (RED,       "运行异常"),
    "starting":     ("#5A6378", "正在启动…"),
}


# ---------- 高清头像 ----------
def _avatar_path(cid: int) -> Path:
    return AVATAR_DIR / f"{cid}.png"


def download_avatar(cid: int, en: str, version: str, timeout: float = 8.0) -> Path | None:
    """下载 128px 高清头像。优先 Community Dragon（按英雄 ID，无需 slug），
    其次 Data Dragon（按英文名）。后台线程调用。"""
    dest = _avatar_path(cid)
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    urls = [
        f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
        f"global/default/v1/champion-icons/{cid}.png",
    ]
    if version:
        fname = {"Wukong": "MonkeyKing"}.get(en, en)
        urls.append(f"https://ddragon.leagueoflegends.com/cdn/{version}"
                    f"/img/champion/{quote(fname)}.png")
    for url in urls:
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            if len(r.content) > 1000:
                dest.write_bytes(r.content)
                return dest
        except Exception:
            continue
    return None


class CounterPickerApp:
    # 段位下拉：(显示名, lolalytics tier参数)，全部实测有效
    TIER_OPTS = [
        ("黄金及以上", "gold_plus"),
        ("白金及以上", "platinum_plus"),
        ("翡翠及以上", "emerald_plus"),
        ("钻石及以上", "diamond_plus"),
        ("大师及以上", "master_plus"),
    ]

    def __init__(self, cfg: Config):
        global SCALE
        SCALE = _init_dpi()

        self.cfg = cfg
        self.engine = Engine(cfg)
        self.engine.manual_lane = ""
        self.q: queue.Queue = queue.Queue()
        self.wake = threading.Event()
        self.stop_ev = threading.Event()
        self.photos: dict[tuple, tk.PhotoImage] = {}
        self._rec_key = None
        self._last_recs: tuple[list, str] = ([], "")
        self._rec_gen = 0
        self._snap = None
        self._manual = False
        self._lane = ""
        self._online_pending = False  # 本轮是否正在等待在线数据（用于头像延后）
        self._ban_mode = False        # 当前榜单是否为"我方英雄受克制榜"（锁定Ban推荐）
        # 符文出装
        self._build_info = None       # 当前 BuildInfo（None=未加载）
        self._build_dirty = True      # 数据更新后、build页是否需要重渲染
        self._build_cid = 0           # 当前 build 对应的英雄ID
        self._build_key = None        # 已加载 build 的 (英雄ID,分路)
        self._build_imgs = {}         # build 图标缓存 key -> PhotoImage
        self._auto_switched = False   # 本局是否已自动跳转到符文出装页
        # 全路总览：阵容指纹 -> {lane: (ally_id, enemy_id, ally_winrate, src)}
        self._ov_fp = None
        self._ov_data: dict[str, tuple] = {}
        self._row_widgets: list[tk.Widget] = []
        self._enemy_widgets: list[tk.Widget] = []

        self.root = tk.Tk()
        # 用真实 DPI 校准缩放比
        try:
            s2 = self.root.winfo_fpixels("1i") / 96.0
            if s2 > SCALE * 1.08:
                SCALE = s2
        except Exception:
            pass

        self.root.title("LOL 对位 Counter 助手")
        self.root.configure(bg=BG)
        self.root.geometry(f"{sp(1000)}x{sp(640)}")
        self.root.minsize(sp(860), sp(560))
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        self._setup_style()
        self._build_ui()
        # 段位下拉初值：与配置一致
        for i, (_, t) in enumerate(self.TIER_OPTS):
            if t == self.cfg.tier:
                self.combo_tier.current(i)
                break
        else:
            self.combo_tier.current(2)

        self.t = threading.Thread(target=self._worker, daemon=True)
        self.t.start()
        self.root.after(200, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- 主题 ----------------
    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Vertical.TScrollbar", background=CARD2, troughcolor=CARD,
                     bordercolor=CARD, arrowcolor=MUTED, darkcolor=CARD2,
                     lightcolor=CARD2)
        st.map("Vertical.TScrollbar",
               background=[("active", BORDER)], arrowcolor=[("active", TEXT)])
        st.configure("TCombobox", fieldbackground=CARD2, background=CARD2,
                     foreground=TEXT, arrowcolor=GOLD, bordercolor=BORDER,
                     selectbackground=BORDER, selectforeground=TEXT)
        st.map("TCombobox",
               fieldbackground=[("readonly", CARD2)],
               foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", BORDER)])
        self.root.option_add("*TCombobox*Listbox.background", CARD2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", BORDER)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        # ===== 顶部标题栏 =====
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=sp(14), pady=(sp(10), sp(4)))
        self.dot = tk.Canvas(header, width=sp(12), height=sp(12), bg=BG,
                             highlightthickness=0)
        self.dot.pack(side="left", padx=(0, sp(8)))
        self.dot_id = self.dot.create_oval(sp(2), sp(2), sp(10), sp(10),
                                           fill="#5A6378", outline="")
        self.lbl_status = tk.Label(header, text="正在启动…", bg=BG, fg=TEXT,
                                   font=fnt(10, "bold"))
        self.lbl_status.pack(side="left")
        tk.Label(header, text="LOL COUNTER", bg=BG, fg=GOLD_DIM,
                 font=fnt(9, "bold")).pack(side="right")

        # ===== 顶部按钮行 =====
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=sp(14), pady=(sp(2), sp(6)))
        self.btn_auto = self._mk_button(btns, "↩ 恢复自动识别", self._clear_manual, GOLD_DIM)
        self.btn_auto.pack(side="left")
        tk.Label(btns, text="段位", bg=BG, fg=MUTED,
                 font=fnt(9)).pack(side="left", padx=(sp(12), sp(4)))
        self.var_tier = tk.StringVar()
        self.combo_tier = ttk.Combobox(
            btns, textvariable=self.var_tier, state="readonly",
            values=[t for t, _ in self.TIER_OPTS],
            font=fnt(9), width=8)
        self.combo_tier.pack(side="left")
        self.combo_tier.bind("<<ComboboxSelected>>", self._on_tier_change)
        self.var_top = tk.BooleanVar(value=True)
        tk.Checkbutton(btns, text="窗口置顶", variable=self.var_top, bg=BG, fg=MUTED,
                       selectcolor=CARD, activebackground=BG, activeforeground=TEXT,
                       font=fnt(9), bd=0, command=self._toggle_top).pack(side="right")

        # ===== 双标签页：① 英雄推荐（BP/对线）  ② 符文出装（选齐后整页） =====
        nb_style = ttk.Style()
        nb_style.configure("TNotebook", background=BG, borderwidth=0)
        nb_style.configure("TNotebook.Tab", background=CARD2, foreground=MUTED,
                           padding=(sp(20), sp(7)), font=fnt(10, "bold"))
        nb_style.map("TNotebook.Tab",
                     background=[("selected", CARD)],
                     foreground=[("selected", GOLD)])
        self.lbl_footer = tk.Label(self.root, text="", bg=BG, fg=MUTED,
                                   font=fnt(8), anchor="w")
        self.lbl_footer.pack(side="bottom", fill="x", padx=sp(14),
                             pady=(0, sp(6)))

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=sp(12), pady=(0, sp(4)))
        self.page1 = tk.Frame(self.nb, bg=BG)
        self.page2 = tk.Frame(self.nb, bg=BG)
        self.nb.add(self.page1, text="①  英雄推荐")
        self.nb.add(self.page2, text="②  符文出装")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ================= 页面①：英雄推荐 =================
        # 顶部：手动查询（横排放一行）
        self._build_search_bar(self.page1)

        p1 = tk.Frame(self.page1, bg=BG)
        p1.pack(fill="both", expand=True, padx=sp(4), pady=(0, sp(4)))
        # 左列
        col_l = tk.Frame(p1, bg=BG)
        col_l.pack(side="left", fill="both", expand=True, padx=(0, sp(6)))
        # 右列
        col_r = tk.Frame(p1, bg=BG)
        col_r.pack(side="left", fill="both", expand=True)

        # —— 左列顶部：对局概览（我方 VS 对位） ——
        self.versus_card = self._mk_card(col_l)
        self.versus_card.pack(fill="x", pady=(0, sp(6)))
        vs_top = tk.Frame(self.versus_card, bg=CARD)
        vs_top.pack(fill="x", padx=sp(12), pady=(sp(8), sp(2)))
        self.lbl_target_title = tk.Label(vs_top, text="对位目标", bg=CARD, fg=GOLD,
                                         font=fnt(9, "bold"))
        self.lbl_target_title.pack(side="left")
        self.lbl_badge = tk.Label(vs_top, text="", bg=CARD, fg=TEAL,
                                  font=fnt(9, "bold"))
        self.lbl_badge.pack(side="right")
        vs_body = tk.Frame(self.versus_card, bg=CARD)
        vs_body.pack(fill="x", padx=sp(12), pady=(sp(2), sp(10)))
        my_box = tk.Frame(vs_body, bg=CARD)
        my_box.pack(side="left", fill="y")
        tk.Label(my_box, text="我　方", bg=CARD, fg=MUTED,
                 font=fnt(8)).pack(anchor="w")
        self.lbl_my_avatar = tk.Label(my_box, bg=CARD2,
                                      image=self._placeholder(60, CARD2))
        self.lbl_my_avatar.pack(anchor="w", pady=(sp(2), sp(4)))
        self.lbl_my = tk.Label(my_box, text="等待识别…", bg=CARD, fg=MUTED,
                               font=fnt(10, "bold"), anchor="w",
                               wraplength=sp(130), justify="left")
        self.lbl_my.pack(anchor="w")
        tk.Label(vs_body, text="VS", bg=CARD, fg=GOLD_DIM,
                 font=fnt(13, "bold")).pack(side="left", padx=sp(12))
        tg_box = tk.Frame(vs_body, bg=CARD)
        tg_box.pack(side="left", fill="y")
        self.lbl_target_avatar = tk.Label(tg_box, bg=CARD2,
                                          image=self._placeholder(60, CARD2))
        self.lbl_target_avatar.pack(anchor="w", pady=(sp(12), sp(4)))
        self.lbl_target_name = tk.Label(tg_box, text="—", bg=CARD, fg=TEXT,
                                        font=fnt(10, "bold"), anchor="w")
        self.lbl_target_name.pack(anchor="w")
        self.lbl_target_sub = tk.Label(tg_box, text="进入选将或游戏后自动识别",
                                       bg=CARD, fg=MUTED, font=fnt(8), anchor="w",
                                       wraplength=sp(170), justify="left")
        self.lbl_target_sub.pack(anchor="w", pady=(sp(2), 0))

        # —— 左列底部：敌方阵容（横向 5 头像） ——
        self.enemy_card = self._mk_card(col_l)
        self.enemy_card.pack(fill="x", pady=(0, sp(6)))
        tk.Label(self.enemy_card, text="敌方阵容（点击头像可切换查询目标）",
                 bg=CARD, fg=GOLD, font=fnt(9, "bold")).pack(
            anchor="w", padx=sp(12), pady=(sp(8), sp(4)))
        self.enemy_row = tk.Frame(self.enemy_card, bg=CARD)
        self.enemy_row.pack(fill="x", padx=sp(10), pady=(0, sp(8)))

        # —— 右列顶部：全路对位总览（常驻，未就绪显示提示） ——
        self.overview_card = self._mk_card(col_r)
        self.overview_card.pack(fill="x", pady=(0, sp(6)))
        ov_head = tk.Frame(self.overview_card, bg=CARD)
        ov_head.pack(fill="x", padx=sp(12), pady=(sp(8), sp(2)))
        self.lbl_ov_title = tk.Label(ov_head, text="全 路 对 位", bg=CARD, fg=GOLD,
                                     font=fnt(9, "bold"))
        self.lbl_ov_title.pack(side="left")
        self.lbl_ov_summary = tk.Label(ov_head, text="", bg=CARD, fg=MUTED,
                                       font=fnt(8))
        self.lbl_ov_summary.pack(side="right")
        self.ov_body = tk.Frame(self.overview_card, bg=CARD)
        self.ov_body.pack(fill="x", padx=sp(10), pady=(0, sp(8)))
        self._ov_widgets: list[tk.Widget] = []

        # —— 右列底部：克制推荐（可滚动，占满剩余高度） ——
        self.rec_card = self._mk_card(col_r)
        self.rec_card.pack(fill="both", expand=True)
        rec_head = tk.Frame(self.rec_card, bg=CARD)
        rec_head.pack(fill="x", padx=sp(12), pady=(sp(8), sp(4)))
        self.lbl_rec_title = tk.Label(rec_head, text="克 制 推 荐", bg=CARD, fg=GOLD,
                                      font=fnt(10, "bold"))
        self.lbl_rec_title.pack(side="left")
        self.lbl_source = tk.Label(rec_head, text="", bg=CARD, fg=MUTED,
                                   font=fnt(8))
        self.lbl_source.pack(side="right")
        self.rec_canvas = tk.Canvas(self.rec_card, bg=CARD, highlightthickness=0)
        scroll = ttk.Scrollbar(self.rec_card, orient="vertical",
                               command=self.rec_canvas.yview)
        self.rec_inner = tk.Frame(self.rec_canvas, bg=CARD)
        self.rec_inner.bind(
            "<Configure>",
            lambda e: self.rec_canvas.configure(
                scrollregion=self.rec_canvas.bbox("all")))
        self.rec_win = self.rec_canvas.create_window((0, 0), window=self.rec_inner,
                                                     anchor="nw")
        self.rec_canvas.configure(yscrollcommand=scroll.set)
        self.rec_canvas.pack(side="left", fill="both", expand=True,
                             padx=(sp(8), 0), pady=(0, sp(8)))
        scroll.pack(side="right", fill="y", pady=(0, sp(8)))
        self.rec_canvas.bind_all("<MouseWheel>",
                                 lambda e: self.rec_canvas.yview_scroll(
                                     int(-e.delta / 120), "units"))
        self.rec_canvas.bind("<Configure>", self._on_rec_canvas_configure)
        self.lbl_hint = tk.Label(self.rec_inner, text="等待对位数据…", bg=CARD,
                                 fg=MUTED, font=fnt(10))
        self.lbl_hint.pack(pady=sp(30))

        # ================= 页面②：符文出装（整页） =================
        # 顶部：英雄信息条
        self.build_head_card = self._mk_card(self.page2)
        self.build_head_card.pack(fill="x", padx=sp(6), pady=(sp(6), sp(6)))
        bh = tk.Frame(self.build_head_card, bg=CARD)
        bh.pack(fill="x", padx=sp(14), pady=sp(10))
        self.lbl_build_avatar = tk.Label(bh, bg=CARD2,
                                         image=self._placeholder(64, CARD2))
        self.lbl_build_avatar.pack(side="left", padx=(0, sp(14)))
        bh_txt = tk.Frame(bh, bg=CARD)
        bh_txt.pack(side="left", fill="y")
        self.lbl_build_name = tk.Label(bh_txt, text="尚未确定英雄", bg=CARD, fg=TEXT,
                                       font=fnt(15, "bold"), anchor="w")
        self.lbl_build_name.pack(anchor="w", pady=(sp(4), 0))
        self.lbl_build_meta = tk.Label(bh_txt, text="双方英雄选齐后，在此整页查看符文与出装",
                                       bg=CARD, fg=MUTED, font=fnt(9), anchor="w")
        self.lbl_build_meta.pack(anchor="w", pady=(sp(2), 0))
        # 右侧胜率/场次
        self.lbl_build_stat = tk.Label(bh, text="", bg=CARD, fg=TEAL,
                                       font=fnt(12, "bold"))
        self.lbl_build_stat.pack(side="right")

        # 内容三列：符文 / 装备 / 技能与召唤师
        self.build_body = tk.Frame(self.page2, bg=BG)
        self.build_body.pack(fill="both", expand=True, padx=sp(6))
        self.c_runes = self._mk_card(self.build_body)
        self.c_items = self._mk_card(self.build_body)
        self.c_skill = self._mk_card(self.build_body)
        self.c_runes.pack(side="left", fill="both", expand=True, padx=(0, sp(6)))
        self.c_items.pack(side="left", fill="both", expand=True, padx=(0, sp(6)))
        self.c_skill.pack(side="left", fill="both", expand=True)

        self.lbl_footer = tk.Label(self.root, text="", bg=BG, fg=MUTED,
                                   font=fnt(8), anchor="w")
        self.lbl_footer.pack(fill="x", padx=sp(16), pady=(0, sp(8)))

    def _on_rec_canvas_configure(self, e):
        self.rec_canvas.itemconfigure(self.rec_win, width=e.width - sp(6))

    def _mk_card(self, parent) -> tk.Frame:
        return tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=sp(1))

    def _mk_button(self, parent, text, cmd, color) -> tk.Label:
        lbl = tk.Label(parent, text=text, bg=CARD2, fg=color, font=fnt(9, "bold"),
                       padx=sp(12), pady=sp(5), cursor="hand2",
                       highlightbackground=BORDER, highlightthickness=sp(1))
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=BORDER))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=CARD2))
        return lbl

    # ---------------- 头像 ----------------
    def _placeholder(self, size: int, color: str) -> tk.PhotoImage:
        key = ("_ph", size, color)
        if key not in self.photos:
            img = tk.PhotoImage(width=sp(size), height=sp(size))
            img.put(color, to=(0, 0, sp(size), sp(size)))
            self.photos[key] = img
        return self.photos[key]

    def _get_photo(self, cid: int, en: str, size: int):
        """返回高清头像 PhotoImage（物理像素 sp(size)）。"""
        key = (cid, size)
        if key in self.photos:
            return self.photos[key]
        path = _avatar_path(cid)
        img = None
        if path.exists():
            try:
                if _HAS_PIL:
                    im = Image.open(path).convert("RGBA").resize(
                        (sp(size), sp(size)), Image.LANCZOS)
                    img = ImageTk.PhotoImage(im)
                else:
                    raw = tk.PhotoImage(file=str(path))   # 128px 原图
                    factor = max(1, round(128 / sp(size)))
                    img = raw.subsample(factor) if factor > 1 else raw
            except tk.TclError:
                img = None
        if img is not None:
            self.photos[key] = img
            return img
        return None

    def _avatar_label(self, parent, cid: int, en: str, size: int, bg=CARD2) -> tk.Label:
        lbl = tk.Label(parent, bg=bg)
        img = self._get_photo(cid, en, size)
        if img:
            lbl.configure(image=img, width=sp(size), height=sp(size))
        else:
            lbl.configure(image=self._placeholder(size, bg), text="?", fg=MUTED,
                          compound="center", font=fnt(10, "bold"),
                          width=sp(size), height=sp(size))
        return lbl

    # ---------------- 后台线程 ----------------
    def _worker(self):
        while not self.stop_ev.is_set():
            try:
                snap = self.engine.poll()
                manual = bool(self.engine.manual_enemy_id)
                # 查询模式：手动点选/敌方已锁定 -> 对位推荐（counter）；
                # 我方已锁定但敌方还没目标 -> 显示"最克制我方英雄"的受克制榜（ban）
                ban_mode = (not manual) and bool(snap.my_champ_id) and not snap.auto_enemy_id
                if ban_mode:
                    target = snap.my_champ_id
                    # 用我方分路查对位数据；分路未知则按英雄定位粗略猜测
                    lane = snap.my_lane or self._guess_lane(target)
                else:
                    target = self.engine.target_enemy(snap)
                    lane = self.engine.manual_lane if manual else snap.my_lane
                self._ban_mode = ban_mode
                key = (target, lane or "auto", ban_mode) if target else None

                if target and key != self._rec_key:
                    self._rec_key = key
                    self._rec_gen += 1
                    gen = self._rec_gen
                    # 1) 磁盘缓存优先：6 小时内看过同一对位直接秒出，不发网络
                    cached_recs, cached_src = self.engine.recommender.recommend_cached(
                        target, lane or "top")
                    if cached_recs:
                        self._last_recs = (cached_recs, cached_src)
                        self._online_pending = False
                    else:
                        # 2) 无缓存：离线表立即出首帧（零等待）
                        off_recs, off_src = self.engine.recommender.recommend_offline(
                            target, lane or "top")
                        if off_recs:
                            self._last_recs = (off_recs, off_src)
                        else:
                            self._last_recs = ([], "")
                        self.q.put(("computing", None))
                        # 3) 在线源后台并行竞速（超时5秒），拉到后替换
                        def on_online(recs, src, _gen=gen, _key=key):
                            if _gen == self._rec_gen and _key == self._rec_key:
                                self.q.put(("recs_online", (recs, src)))
                        self.engine.recommender.recommend_async(
                            target, lane or "top", on_online=on_online, quick_timeout=5.0)
                        self._online_pending = True
                elif not target:
                    self._rec_key = None
                    self._last_recs = ([], "")
                    self._online_pending = False
                    self._ban_mode = False

                # ---- 符文出装：优先展示我方英雄；我方未知时展示对位目标 ----
                build_cid = (snap.my_champ_id
                             or (target if not ban_mode else None))
                build_lane = (snap.my_lane
                              if snap.my_champ_id else (lane or "auto"))
                if build_cid:
                    bkey = (build_cid, build_lane or "auto")
                    if bkey != self._build_key:
                        bc = self.engine.recommender.build_cached(
                            build_cid, build_lane if build_lane != "auto" else "top")
                        if bc:
                            self._build_key = bkey
                            self._build_info = bc
                            self.q.put(("build_ready", bc))
                        else:
                            # 先锁定 key，避免回包时与旧 key 校验失败而丢弃新数据
                            self._build_key = bkey
                            self._build_info = None
                            self.q.put(("build_loading", bkey))
                            def _do_fetch(_cid=build_cid,
                                          _ln=(build_lane if build_lane != "auto" else "top"),
                                          _bkey=bkey):
                                try:
                                    bi = self.engine.recommender.build_fetch(_cid, _ln)
                                except Exception:
                                    bi = None
                                # 仅当玩家仍停留在同一英雄时采用结果
                                if bi and _bkey == self._build_key:
                                    self.q.put(("build_ready", bi))
                            threading.Thread(target=_do_fetch, daemon=True).start()
                else:
                    if self._build_key is not None:
                        self._build_key = None
                        self._build_info = None
                        self.q.put(("build_hide", None))

                # 预下载本次需要的高清头像
                ens: dict[int, str] = {}
                if snap.my_champ_id:
                    info = self.engine.db.by_champion_id(snap.my_champ_id)
                    if info:
                        ens[snap.my_champ_id] = info["en"]
                for e in snap.enemies:
                    info = self.engine.db.by_champion_id(e.champion_id)
                    if info:
                        ens[e.champion_id] = info["en"]
                if target:
                    info = self.engine.db.by_champion_id(target)
                    if info:
                        ens[target] = info["en"]
                for c in self._last_recs[0]:
                    # ban 模式下跳过已被 ban 的英雄
                    if ban_mode and c.champion_id in snap.bans:
                        continue
                    # 正在拉在线数据时跳过推荐头像：避免与排行榜请求抢带宽；
                    # 下一轮（数据已就绪）会自动补下载
                    if not self._online_pending:
                        ens[c.champion_id] = self.engine.db.en_of(c.champion_id)
                got_new = False
                for cid, en in ens.items():
                    if download_avatar(cid, en, self.engine.db.version or "",
                                       self.cfg.http_timeout):
                        got_new = True
                self.q.put(("state", (snap, self._last_recs, manual, lane)))
                if got_new:
                    self.q.put(("avatars", None))

                # ---- 全路对位总览：双方各锁定至少 3 人时才查 ----
                self._maybe_overview(snap)

                # ---- 双方 10 人选齐且我方已锁定：自动跳到符文出装页 ----
                full = (snap.my_champ_id
                        and len(snap.allies) >= 5
                        and len(snap.enemies) >= 5)
                if full:
                    if not self._auto_switched:
                        self._auto_switched = True
                        self.q.put(("goto_build", None))
                else:
                    # 阵容未满（新一局/退回选将）：重置，允许下一局再次自动跳
                    if self._auto_switched:
                        self._auto_switched = False
                        self.q.put(("goto_rec", None))
            except Exception as e:
                self.q.put(("error", str(e)))
            self.wake.wait(max(1.0, self.cfg.poll_interval))
            self.wake.clear()

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "state":
                    snap, recs, manual, lane = payload
                    self._snap, self._last_recs = snap, recs
                    self._manual, self._lane = manual, lane
                    self._render()
                elif kind == "recs_online":
                    recs, src = payload
                    self._last_recs = (recs, src)
                    if self._snap is not None:
                        self._render()
                elif kind == "computing":
                    if not self._last_recs[0]:
                        self.lbl_hint.configure(text="正在分析对位数据…")
                elif kind == "avatars":
                    if self._snap is not None:
                        self._render()
                elif kind == "error":
                    self.lbl_status.configure(text=f"运行异常：{payload}")
                elif kind == "build_loading":
                    self.lbl_build_meta.configure(text="正在获取符文与出装…")
                elif kind == "build_ready":
                    self._build_info = payload
                    self._build_cid = payload.champion_id
                    self._build_key = (payload.champion_id, payload.lane)
                    self._build_dirty = True
                    if self.nb.index("current") == 1:
                        self._render_build()
                elif kind == "build_hide":
                    self._build_info = None
                    self._build_cid = 0
                    self._build_key = None
                    self._build_dirty = True
                    self._reset_build_placeholder()
                elif kind == "ov_show":
                    self._render_overview()
                elif kind == "ov_hide":
                    self._render_overview_placeholder()
                elif kind == "ov_row":
                    self._on_ov_row(payload)
                elif kind == "goto_build":
                    self._switch_to_build_tab()
                elif kind == "goto_rec":
                    if self.nb.index("current") != 0:
                        self.nb.select(0)
        except queue.Empty:
            pass
        self.root.after(200, self._drain_queue)

    # ---------------- 符文出装 ----------------
    def _on_tab_changed(self, _evt=None):
        # 切到符文出装页且数据是新的，就重渲染整页
        if self.nb.index("current") == 1 and self._build_info \
                and self._build_dirty:
            self._render_build()

    def _switch_to_build_tab(self):
        if self.nb.index("current") != 1:
            self.nb.select(1)

    def _reset_build_placeholder(self):
        for col in (self.c_runes, self.c_items, self.c_skill):
            for w in col.winfo_children():
                w.destroy()
        self.lbl_build_name.configure(text="尚未确定英雄")
        self.lbl_build_meta.configure(text="双方英雄选齐后，在此整页查看符文与出装")
        self.lbl_build_stat.configure(text="")
        ph = self._placeholder(64, CARD2)
        self.lbl_build_avatar.configure(image=ph, width=sp(64), height=sp(64))
        self.lbl_build_avatar.image = ph

    def _load_local_image(self, path, size: int):
        """本地图片文件 -> 缩略 PhotoImage（支持 png/webp）。"""
        try:
            if _HAS_PIL:
                im = Image.open(str(path)).convert("RGBA").resize(
                    (sp(size), sp(size)), Image.LANCZOS)
                return ImageTk.PhotoImage(im)
            return tk.PhotoImage(file=str(path))
        except Exception:
            return None

    def _build_img(self, url: str, size: int):
        """下载/取缓存 build 图标 URL -> PhotoImage（失败返回 None）。"""
        if not url:
            return None
        ck = f"{url}@{size}"
        if ck in self._build_imgs:
            return self._build_imgs[ck]
        p = self._fetch_url_image(url)
        img = self._load_local_image(p, size) if p else None
        if img is not None:
            self._build_imgs[ck] = img
        return img

    def _fetch_url_image(self, url: str):
        """下载 URL 图标到本地图标缓存，返回路径（已存在则直接返回）。"""
        import hashlib
        ext = ".png" if ".png" in url.lower() else ".webp"
        h = hashlib.md5(url.encode()).hexdigest()[:16]
        path = ICON_DIR / f"b_{h}{ext}"
        if path.exists():
            return path
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and r.content:
                path.write_bytes(r.content)
                return path
        except Exception:
            return None
        return None

    def _render_build(self):
        b = self._build_info
        if not b:
            return
        for col in (self.c_runes, self.c_items, self.c_skill):
            for w in col.winfo_children():
                w.destroy()

        def col_title(parent, text):
            tk.Label(parent, text=text, bg=CARD, fg=GOLD,
                     font=fnt(11, "bold"), anchor="w").pack(
                anchor="w", padx=sp(14), pady=(sp(12), sp(6)))

        def icon_cell(parent, url, size, bg=CARD):
            im = self._build_img(url, size)
            lbl = tk.Label(parent, bg=bg)
            if im:
                lbl.configure(image=im, width=sp(size), height=sp(size))
            else:
                lbl.configure(image=self._placeholder(size, CARD2),
                              width=sp(size), height=sp(size))
            return lbl

        def icon_row(parent, urls, size, gap=6):
            row = tk.Frame(parent, bg=CARD)
            row.pack(anchor="w", padx=sp(14), pady=(0, sp(6)))
            for u in urls:
                icon_cell(row, u, size).pack(side="left", padx=(0, sp(gap)))
            return row

        # ---------- 第1列：符文 ----------
        col_title(self.c_runes, "符文")
        # 基石符文
        kf = tk.Frame(self.c_runes, bg=CARD)
        kf.pack(anchor="w", padx=sp(14), pady=(0, sp(10)))
        icon_cell(kf, b.keystone_url, 52).pack(side="left")
        tk.Label(kf, text="基石符文", bg=CARD, fg=MUTED,
                 font=fnt(9)).pack(side="left", padx=sp(10))
        # 小符文：按系分组
        trees: dict[str, list] = {}
        for r in b.runes:
            trees.setdefault(r.get("tree", ""), []).append(r["url"])
        for urls in trees.values():
            icon_row(self.c_runes, urls, 32, gap=6)
        # 属性碎片
        tk.Label(self.c_runes, text="属性碎片", bg=CARD, fg=MUTED,
                 font=fnt(8), anchor="w").pack(anchor="w", padx=sp(14),
                                               pady=(sp(6), sp(2)))
        icon_row(self.c_runes, b.shards, 24, gap=6)

        # ---------- 第2列：出装 ----------
        col_title(self.c_items, "出装")
        if b.start_items:
            tk.Label(self.c_items, text="出门装", bg=CARD, fg=MUTED,
                     font=fnt(8), anchor="w").pack(anchor="w", padx=sp(14),
                                                   pady=(0, sp(2)))
            icon_row(self.c_items, b.start_items, 38)
        tk.Label(self.c_items, text="核心装备", bg=CARD, fg=MUTED,
                 font=fnt(8), anchor="w").pack(anchor="w", padx=sp(14),
                                               pady=(sp(8), sp(2)))
        icon_row(self.c_items, b.core_items, 40)
        if b.boots_url:
            tk.Label(self.c_items, text="鞋子", bg=CARD, fg=MUTED,
                     font=fnt(8), anchor="w").pack(anchor="w", padx=sp(14),
                                                   pady=(sp(8), sp(2)))
            icon_row(self.c_items, [b.boots_url], 38)

        # ---------- 第3列：召唤师技能 + 技能加点 ----------
        col_title(self.c_skill, "召唤师技能")
        sprow = tk.Frame(self.c_skill, bg=CARD)
        sprow.pack(anchor="w", padx=sp(14), pady=(0, sp(10)))
        for u in b.summoner:
            cell = tk.Frame(sprow, bg=CARD)
            cell.pack(side="left", padx=(0, sp(10)))
            icon_cell(cell, u, 42).pack()
        if b.skill_order:
            tk.Label(self.c_skill, text="技能加点（前 5 级）", bg=CARD, fg=MUTED,
                     font=fnt(8), anchor="w").pack(anchor="w", padx=sp(14),
                                                   pady=(sp(6), sp(4)))
            grid = tk.Frame(self.c_skill, bg=CARD)
            grid.pack(anchor="w", padx=sp(14))
            for i, sk in enumerate(b.skill_order, 1):
                cell = tk.Frame(grid, bg=CARD2)
                cell.pack(side="left", padx=(0, sp(5)))
                tk.Label(cell, text=str(i), bg=CARD2, fg=MUTED,
                         font=fnt(7)).pack(padx=sp(4), pady=(sp(2), 0))
                tk.Label(cell, text=sk, bg=CARD2, fg=TEAL,
                         font=fnt(11, "bold")).pack(padx=sp(5), pady=(0, sp(3)))

        # ---------- 顶部英雄信息条 ----------
        cid = getattr(self, "_build_cid", 0)
        if cid:
            info = self.engine.db.by_champion_id(cid)
            self.lbl_build_name.configure(text=info["name"])
            lane_txt = LANE_CN.get(b.lane, "")
            meta = f"{info['en']}" + (f"　｜　{lane_txt}" if lane_txt else "")
            meta += f"　｜　版本 {b.patch}" if b.patch else ""
            self.lbl_build_meta.configure(text=meta)
            av = self._get_photo(cid, info["en"], 64)
            if av:
                self.lbl_build_avatar.configure(
                    image=av, width=sp(64), height=sp(64))
                self.lbl_build_avatar.image = av
        stat = ""
        if b.win_rate:
            stat = f"{b.win_rate:.1f}% 胜率"
            if b.games:
                stat += f"\n{b.games:,} 场"
        self.lbl_build_stat.configure(text=stat, justify="right")
        self._build_dirty = False

    # ---------------- 渲染（原） ----------------
    def _render(self):
        snap = self._snap
        recs, source = self._last_recs
        ban = self._ban_mode
        if ban and snap:
            target = snap.my_champ_id
        else:
            target = self.engine.target_enemy(snap) if snap else 0

        color, phase_txt = PHASE_DOT.get(snap.phase if snap else "starting",
                                         PHASE_DOT["starting"])
        self.dot.itemconfigure(self.dot_id, fill=color)
        self.lbl_status.configure(text=phase_txt)

        if snap and snap.my_champ_id:
            info = self.engine.db.by_champion_id(snap.my_champ_id)
            lane_txt = f"｜{LANE_CN.get(snap.my_lane, '')}" if snap.my_lane else ""
            self.lbl_my.configure(text=f"{info['name']}{lane_txt}",
                                  fg=TEXT)
            my_img = self._get_photo(snap.my_champ_id, info["en"], 64)
            if my_img:
                self.lbl_my_avatar.configure(
                    image=my_img, width=sp(64), height=sp(64))
                self.lbl_my_avatar.image = my_img
            else:
                self.lbl_my_avatar.configure(
                    image=self._placeholder(64, CARD2), width=sp(64), height=sp(64))
        else:
            self.lbl_my.configure(text="等待识别…", fg=MUTED)
            self.lbl_my_avatar.configure(
                image=self._placeholder(64, CARD2), text="?", fg=MUTED,
                compound="center", font=fnt(14, "bold"),
                width=sp(64), height=sp(64))

        if ban:
            self.lbl_target_title.configure(text="我的英雄")
        else:
            self.lbl_target_title.configure(text="对位目标")

        if target:
            info = self.engine.db.by_champion_id(target)
            self.lbl_target_name.configure(text=info["name"])
            if ban:
                sub = f"{info['en']}｜下列英雄最克制 TA"
            else:
                qlane = self._lane or self._guess_lane(target)
                sub = f"{info['en']} · {LANE_CN.get(qlane, '自动')}"
            self.lbl_target_sub.configure(text=sub, fg=MUTED)
            if ban:
                self.lbl_badge.configure(text="● 受克制榜", fg=RED)
            else:
                self.lbl_badge.configure(text="● 手动选择" if self._manual else "● 自动识别",
                                         fg=GOLD if self._manual else TEAL)
            self._set_target_avatar(target, info["en"])
        else:
            self.lbl_target_name.configure(text="—")
            self.lbl_target_sub.configure(text="进入选将或游戏后自动识别，也可手动查询",
                                          fg=MUTED)
            self.lbl_badge.configure(text="")
            self.lbl_target_avatar.configure(
                image=self._placeholder(64, CARD2), text="?", fg=MUTED,
                compound="center", font=fnt(14, "bold"),
                width=sp(64), height=sp(64))

        self._render_enemies(snap, target)
        self._render_recs(recs, source, target)

        self.lbl_footer.configure(
            text=f"更新于 {time.strftime('%H:%M:%S')}　·　数据源：{source or '—'}"
                 f"　·　只读本地接口，不读内存不注入")

    def _guess_lane(self, cid: int) -> str:
        info = self.engine.db.by_champion_id(cid)
        if not info:
            return ""
        tags = [t.lower() for t in info.get("tags", [])]
        if "marksman" in tags:
            return "bottom"
        if "support" in tags:
            return "support"
        if "fighter" in tags or "tank" in tags:
            return "top"
        if "assassin" in tags:
            return "jungle"
        return "middle"

    def _set_target_avatar(self, cid: int, en: str):
        img = self._get_photo(cid, en, 64)
        if img:
            self.lbl_target_avatar.configure(image=img, text="",
                                             width=sp(64), height=sp(64))
            self.lbl_target_avatar.image = img
        else:
            self.lbl_target_avatar.configure(
                image=self._placeholder(64, CARD2), text="?", fg=MUTED,
                compound="center", font=fnt(14, "bold"),
                width=sp(64), height=sp(64))

    def _render_enemies(self, snap, target):
        for w in self._enemy_widgets:
            w.destroy()
        self._enemy_widgets = []
        if not snap or not snap.enemies:
            lbl = tk.Label(self.enemy_row, text="尚未检测到敌方英雄", bg=CARD,
                           fg=MUTED, font=fnt(9))
            lbl.pack(side="left", padx=sp(4), pady=sp(6))
            self._enemy_widgets.append(lbl)
            return
        for e in snap.enemies:
            info = self.engine.db.by_champion_id(e.champion_id)
            en, cid = (info["en"], e.champion_id) if info else ("?", e.champion_id)
            box = tk.Frame(self.enemy_row, bg=CARD)
            box.pack(side="left", padx=sp(6), pady=sp(4))
            avatar = self._avatar_label(box, cid, en, 44)
            avatar.pack()
            tk.Label(box, text=(info["name"] if info else "?")[:5], bg=CARD,
                     fg=TEXT, font=fnt(8)).pack()
            tk.Label(box, text=e.lane_cn or "未定", bg=CARD, fg=MUTED,
                     font=fnt(7)).pack()
            is_target = (e.champion_id == target)
            avatar.configure(highlightthickness=sp(2) if is_target else 0,
                             highlightbackground=GOLD if is_target else CARD,
                             cursor="hand2")
            avatar.bind("<Button-1>",
                        lambda ev, c=e.champion_id, ln=e.lane:
                        self._select_manual(c, ln))
            self._enemy_widgets.extend([box, avatar] + box.winfo_children())

    def _render_recs(self, recs, source, target):
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets = []
        self.lbl_hint.pack_forget()

        ban = self._ban_mode
        if ban:
            # ban 模式：去掉已经被 ban 掉的英雄
            banned = set(self._snap.bans if self._snap else [])
            recs = [c for c in recs if c.champion_id not in banned]

        self.lbl_rec_title.configure(text="受 克 制 榜" if ban else "克 制 推 荐")

        if not target:
            self.lbl_hint.configure(text="进入选将阶段或游戏后，这里会给出克制推荐")
            self.lbl_hint.pack(pady=sp(30))
            self.lbl_source.configure(text="")
            return
        if not recs:
            self.lbl_hint.configure(
                text="正在获取对位数据…\n（国内网络访问国际数据源较慢时，\n"
                     "会自动切换到内置离线克制表）")
            self.lbl_hint.pack(pady=sp(30))
            self.lbl_source.configure(
                text=source or "在线数据获取中…（通常 3～5 秒）")
            return

        rank_colors = {1: GOLD, 2: "#C0C0C0", 3: "#CD7F32"}
        for i, c in enumerate(recs, 1):
            row_bg = CARD2 if i % 2 else CARD
            row = tk.Frame(self.rec_inner, bg=row_bg)
            row.pack(fill="x", padx=sp(6), pady=sp(2), ipady=sp(4))

            rk = tk.Label(row, text=str(i), bg=row_bg,
                          fg=rank_colors.get(i, MUTED),
                          font=fnt(11, "bold"), width=2)
            rk.pack(side="left", padx=(sp(6), sp(2)))

            en = self.engine.db.en_of(c.champion_id)
            avatar = self._avatar_label(row, c.champion_id, en, 40, bg=row_bg)
            avatar.pack(side="left", padx=sp(6))

            name_box = tk.Frame(row, bg=row_bg)
            name_box.pack(side="left", fill="y")
            tk.Label(name_box, text=c.name, bg=row_bg, fg=TEXT,
                     font=fnt(10, "bold"), anchor="w").pack(anchor="w",
                                                            pady=(sp(4), 0))
            tk.Label(name_box, text=en, bg=row_bg, fg=MUTED,
                     font=fnt(7), anchor="w").pack(anchor="w")

            # 胜率语义：counter 模式显示我方对位胜率（越高越好）；
            # ban 模式显示我方英雄对其胜率（越低=越被克制，越该 ban）
            disp = c.enemy_winrate if ban else c.my_winrate
            col = (self._ban_wr_color(disp) if ban else self._wr_color(disp))

            pct = tk.Label(row, text=f"{disp:.1f}%", bg=row_bg,
                           fg=col, font=fnt(12, "bold"))
            pct.pack(side="right", padx=sp(10))

            bar_w, bar_h = sp(92), sp(10)
            bar = tk.Canvas(row, width=bar_w, height=bar_h, bg=row_bg,
                            highlightthickness=0)
            bar.pack(side="right", padx=sp(4))
            bar.create_rectangle(0, sp(3), bar_w, sp(9), fill=BORDER, outline="")
            # 进度条统一表示"克制强度"：ban 模式胜率越低条越长
            if ban:
                frac = max(0.05, min(1.0, (54.0 - disp) / 10.0))
            else:
                frac = max(0.05, min(1.0, (disp - 46.0) / 10.0))
            bar.create_rectangle(0, sp(3), int(bar_w * frac), sp(9),
                                 fill=col, outline="")
            self._row_widgets.extend([row, rk, avatar, name_box, pct, bar]
                                     + name_box.winfo_children())

        tag = "在线数据" if source in ("lolalytics", "blitz") else "离线表"
        if ban:
            note = "建议队友 Ban　·　胜率为我方英雄对其胜率（越低越该 Ban）"
        else:
            note = "按克制幅度排序（胜率为我方对位胜率）"
        self.lbl_source.configure(text=f"数据源：{tag}　·　{note}")

    @staticmethod
    def _wr_color(wr: float) -> str:
        if wr >= 53:
            return GREEN
        if wr >= 51:
            return TEAL
        if wr >= 50:
            return GOLD_DIM
        return MUTED

    @staticmethod
    def _ban_wr_color(wr: float) -> str:
        # 我方胜率越低=越被克制，越该 ban，颜色越红
        if wr <= 47:
            return RED
        if wr <= 49:
            return "#E8804A"
        if wr <= 50:
            return GOLD_DIM
        return MUTED

    # ---------------- 全路对位总览 ----------------
    def _maybe_overview(self, snap):
        """双方各锁定至少 3 人才触发；阵容指纹变化时后台批量查询。"""
        if len(snap.allies) < 3 or len(snap.enemies) < 3:
            if self._ov_fp is not None:
                self._ov_fp = None
                self._ov_data = {}
                self.q.put(("ov_hide", None))
            return
        pairs = self.engine.lane_matchups(snap)
        fp = tuple(sorted(
            (a.champion_id if a else 0, e.champion_id if e else 0)
            for _, a, e in pairs))
        if fp == self._ov_fp:
            return
        self._ov_fp = fp
        self._ov_data = {}
        self.q.put(("ov_show", None))

        tier = self.engine.recommender.tier
        db = self.engine.db

        def work():
            from .matchup import fetch_hero_rows
            import concurrent.futures
            tasks = [(ln, a, e) for ln, a, e in pairs if a and e]

            def one(item):
                ln, a, e = item
                rows, src = fetch_hero_rows(db, a.champion_id, ln, tier, 5.0)
                if not rows:
                    return None
                for row in rows:
                    rid = db.id_by_en(str(row["enemy_name"]).strip())
                    if rid == e.champion_id:
                        return (ln, a.champion_id, e.champion_id,
                                float(row["winrate"]), src)
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                futs = [ex.submit(one, t) for t in tasks]
                for f in concurrent.futures.as_completed(futs):
                    r = f.result()
                    if r:
                        self.q.put(("ov_row", r))

        threading.Thread(target=work, daemon=True).start()

    def _on_ov_row(self, row):
        ln, aid, eid, wr, src = row
        self._ov_data[ln] = (aid, eid, wr, src)
        self._render_overview()

    def _render_overview_placeholder(self, text="双方各锁定 3 人后显示全路对位"):
        for w in self._ov_widgets:
            w.destroy()
        self._ov_widgets = []
        tip = tk.Label(self.ov_body, text=text, bg=CARD, fg=MUTED,
                       font=fnt(9))
        tip.pack(pady=sp(14))
        self._ov_widgets.append(tip)
        self.lbl_ov_summary.configure(text="")

    def _render_overview(self):
        for w in self._ov_widgets:
            w.destroy()
        self._ov_widgets = []
        if not self._snap:
            self._render_overview_placeholder()
            return
        if len(self._snap.allies) < 3 or len(self._snap.enemies) < 3:
            self._render_overview_placeholder()
            return
        pairs = self.engine.lane_matchups(self._snap)
        win_n = lose_n = 0
        for ln, a, e in pairs:
            if not (a and e):
                continue
            row_bg = CARD2
            row = tk.Frame(self.ov_body, bg=row_bg)
            row.pack(fill="x", pady=sp(1), ipady=sp(2))
            tk.Label(row, text=LANE_CN.get(ln, ""), bg=row_bg, fg=GOLD,
                     font=fnt(9, "bold"), width=4).pack(side="left", padx=(sp(4), 0))
            self._avatar_label(row, a.champion_id,
                               self.engine.db.en_of(a.champion_id), 26, bg=row_bg
                               ).pack(side="left", padx=sp(4))
            d = self._ov_data.get(ln)
            if d:
                wr = d[2]
                if wr >= 52:
                    tag, col, win_n = "优", GREEN, win_n + 1
                elif wr <= 48:
                    tag, col, lose_n = "劣", RED, lose_n + 1
                else:
                    tag, col = "均", GOLD_DIM
                mid = tk.Label(row, text=f"{wr:.1f}% {tag}", bg=row_bg, fg=col,
                               font=fnt(9, "bold"))
            else:
                mid = tk.Label(row, text="查询中…", bg=row_bg, fg=MUTED,
                               font=fnt(8))
            mid.pack(side="left", padx=sp(6))
            self._avatar_label(row, e.champion_id,
                               self.engine.db.en_of(e.champion_id), 26, bg=row_bg
                               ).pack(side="left", padx=sp(4))
            self._ov_widgets.append(row)
            self._ov_widgets.extend(row.winfo_children())
        self.lbl_ov_summary.configure(
            text=f"我方优势 {win_n} 路　·　劣势 {lose_n} 路")

    # ---------------- 交互 ----------------
    def _select_manual(self, cid: int, lane: str):
        self.engine.manual_enemy_id = int(cid)
        self.engine.manual_lane = lane if lane in LANE_CN else ""
        self._rec_key = None
        # 同步内嵌搜索框显示
        info = self.engine.db.by_champion_id(int(cid))
        if info:
            alias = info.get("alias", "")
            extra = alias.split()[0] if alias else ""
            head = info["name"] + (f"（{extra}）" if extra else "")
            self.var_search.set(f"{head}｜{info['en']}")
        if lane:
            self.var_search_lane.set(LANE_CN.get(lane, "自动"))
        self.wake.set()

    def _clear_manual(self):
        self.engine.manual_enemy_id = 0
        self.engine.manual_lane = ""
        self._rec_key = None
        try:
            self.var_search.set("")
            self.var_search_lane.set("自动")
        except AttributeError:
            pass
        self.wake.set()

    def _toggle_top(self):
        try:
            self.root.attributes("-topmost", self.var_top.get())
        except tk.TclError:
            pass

    def _on_tier_change(self, _event=None):
        """切换段位：更新配置与查询引擎，并让当前对位/出装按新段位重查。"""
        idx = self.combo_tier.current()
        if idx < 0:
            return
        tier = self.TIER_OPTS[idx][1]
        self.cfg.tier = tier
        self.engine.recommender.tier = tier
        self.cfg.save()
        # 清空缓存键，下一轮 worker 检测到不一致即按新段位重新拉取
        self._rec_key = None
        self._build_key = None

    # ---------------- 内嵌手动查询栏 ----------------
    SEARCH_LANE_OPTS = [("自动", ""), ("上单", "top"), ("打野", "jungle"),
                        ("中单", "middle"), ("下路", "bottom"), ("辅助", "support")]

    def _build_search_bar(self, parent):
        card = self._mk_card(parent)
        card.pack(fill="x", padx=sp(4), pady=(sp(2), sp(6)))

        self.engine.db.load()
        items = []
        for info in self.engine.db.by_id.values():
            alias = info.get("alias", "")
            title = info["title"] if info["title"] != info["name"] else ""
            extra = alias.split()[0] if alias else title
            head = info["name"] + (f"（{extra}）" if extra else "")
            items.append(f"{head}｜{info['en']}")
        self._champ_names = sorted(set(items))

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=sp(10), pady=sp(8))
        tk.Label(row, text="手动查询", bg=CARD, fg=GOLD,
                 font=fnt(9, "bold")).pack(side="left", padx=(0, sp(8)))
        self.var_search = tk.StringVar()
        self.combo_search = ttk.Combobox(row, textvariable=self.var_search,
                                         values=self._champ_names, font=fnt(10))
        self.combo_search.pack(side="left", fill="x", expand=True)
        self.combo_search.bind("<KeyRelease>", self._filter_champs)
        self.combo_search.bind("<Return>", lambda e: self._do_search())
        tk.Label(row, text="分路", bg=CARD, fg=MUTED,
                 font=fnt(9)).pack(side="left", padx=(sp(10), sp(4)))
        self.var_search_lane = tk.StringVar(value="自动")
        self.combo_lane = ttk.Combobox(
            row, textvariable=self.var_search_lane, state="readonly",
            values=[t for t, _ in self.SEARCH_LANE_OPTS], font=fnt(9), width=6)
        self.combo_lane.pack(side="left")
        self._mk_button(row, "查询", self._do_search, TEAL).pack(
            side="left", padx=(sp(8), 0))

    def _filter_champs(self, _evt=None):
        q = self.var_search.get().strip().lower()
        if not q:
            self.combo_search["values"] = self._champ_names
            return
        hit = self._find_champ(q)
        if hit:
            info = self.engine.db.by_champion_id(hit)
            top = [n for n in self._champ_names if n.startswith(info["name"])][:1]
            rest = [n for n in self._champ_names if q in n.lower()
                    and not n.startswith(info["name"])]
            self.combo_search["values"] = top + rest[:12]
        else:
            self.combo_search["values"] = [
                n for n in self._champ_names if q in n.lower()][:13]

    def _do_search(self):
        cid = self._find_champ(self.var_search.get())
        if not cid:
            messagebox.showwarning("未找到",
                                   f"找不到英雄：{self.var_search.get()}")
            return
        lane_txt = self.var_search_lane.get()
        lane = dict(self.SEARCH_LANE_OPTS).get(lane_txt, "")
        self._select_manual(cid, lane)

    def _find_champ(self, text: str):
        raw = text.strip()
        if not raw:
            return None
        parts = [p.strip().lower() for p in raw.replace("|", "｜").split("｜")
                 if p.strip()]
        parts.append(raw.lower())

        def fields(info):
            en = info["en"].lower()
            return [info["name"].lower(), en, en.replace(" ", ""),
                    info.get("alias", "").lower()]

        for q in parts:
            qc = q.replace(" ", "")
            for info in self.engine.db.by_id.values():
                if any(q == f or qc == f.replace(" ", "")
                       for f in fields(info) if f):
                    return info["id"]

        def score_of(info, q):
            name, en, alias = (info["name"].lower(), info["en"].lower(),
                               info.get("alias", "").lower())
            tokens = alias.split()
            if q in tokens:
                return 50 - len(name)
            if q in name:
                return 40 - len(name)
            if q in en:
                return 30 - len(name)
            if q in alias:
                return 20 - len(name)
            return -1

        best, best_score = None, -1
        for q in parts:
            if len(q) < 2:
                continue
            for info in self.engine.db.by_id.values():
                s = score_of(info, q)
                if s > best_score:
                    best, best_score = info["id"], s
        return best

    def _on_close(self):
        self.stop_ev.set()
        self.wake.set()
        self.root.after(200, self.root.destroy)

    def run(self):
        self.root.mainloop()
