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

AVATAR_DIR = CACHE_DIR / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

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
        self.root.geometry(f"{sp(440)}x{sp(860)}")
        self.root.minsize(sp(420), sp(760))
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        self._setup_style()
        self._build_ui()

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
        # 顶部标题栏
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=sp(14), pady=(sp(12), sp(6)))
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

        # 按钮行
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=sp(14), pady=(sp(2), sp(6)))
        self.btn_auto = self._mk_button(btns, "↩ 恢复自动识别", self._clear_manual, GOLD_DIM)
        self.btn_auto.pack(side="left")
        self.var_top = tk.BooleanVar(value=True)
        tk.Checkbutton(btns, text="窗口置顶", variable=self.var_top, bg=BG, fg=MUTED,
                       selectcolor=CARD, activebackground=BG, activeforeground=TEXT,
                       font=fnt(9), bd=0, command=self._toggle_top).pack(side="right")

        # 手动查询行（内嵌主窗口，不再弹独立窗口）
        self._build_search_bar()

        # 我方信息
        self.my_frame = self._mk_card(self.root)
        self.my_frame.pack(fill="x", padx=sp(12), pady=(0, sp(8)))
        tk.Label(self.my_frame, text="我　方", bg=CARD, fg=GOLD,
                 font=fnt(9, "bold")).pack(anchor="w", padx=sp(12), pady=(sp(8), sp(2)))
        self.lbl_my = tk.Label(self.my_frame, text="等待识别…", bg=CARD, fg=MUTED,
                               font=fnt(11))
        self.lbl_my.pack(anchor="w", padx=sp(12), pady=(0, sp(10)))

        # 对位目标卡片
        self.target_card = self._mk_card(self.root)
        self.target_card.pack(fill="x", padx=sp(12), pady=(0, sp(8)))
        top_row = tk.Frame(self.target_card, bg=CARD)
        top_row.pack(fill="x", padx=sp(12), pady=(sp(8), sp(2)))
        tk.Label(top_row, text="对位目标", bg=CARD, fg=GOLD,
                 font=fnt(9, "bold")).pack(side="left")
        self.lbl_badge = tk.Label(top_row, text="", bg=CARD, fg=TEAL,
                                  font=fnt(9, "bold"))
        self.lbl_badge.pack(side="right")

        body = tk.Frame(self.target_card, bg=CARD)
        body.pack(fill="x", padx=sp(12), pady=(sp(2), sp(10)))
        self.lbl_target_avatar = tk.Label(body, bg=CARD2,
                                          image=self._placeholder(76, CARD2))
        self.lbl_target_avatar.pack(side="left", padx=(0, sp(12)))
        txt_box = tk.Frame(body, bg=CARD)
        txt_box.pack(side="left", fill="both", expand=True)
        self.lbl_target_name = tk.Label(txt_box, text="—", bg=CARD, fg=TEXT,
                                        font=fnt(15, "bold"), anchor="w")
        self.lbl_target_name.pack(anchor="w", pady=(sp(6), 0))
        self.lbl_target_sub = tk.Label(txt_box, text="进入选将或游戏后自动识别",
                                       bg=CARD, fg=MUTED, font=fnt(9), anchor="w")
        self.lbl_target_sub.pack(anchor="w", pady=(sp(2), 0))

        # 敌方阵容
        self.enemy_card = self._mk_card(self.root)
        self.enemy_card.pack(fill="x", padx=sp(12), pady=(0, sp(8)))
        tk.Label(self.enemy_card, text="敌方阵容（点击头像可切换查询目标）",
                 bg=CARD, fg=GOLD, font=fnt(9, "bold")).pack(
            anchor="w", padx=sp(12), pady=(sp(8), sp(4)))
        self.enemy_row = tk.Frame(self.enemy_card, bg=CARD)
        self.enemy_row.pack(fill="x", padx=sp(12), pady=(0, sp(10)))

        # 推荐区（可滚动）
        rec_card = self._mk_card(self.root)
        rec_card.pack(fill="both", expand=True, padx=sp(12), pady=(0, sp(8)))
        rec_head = tk.Frame(rec_card, bg=CARD)
        rec_head.pack(fill="x", padx=sp(12), pady=(sp(8), sp(4)))
        tk.Label(rec_head, text="克 制 推 荐", bg=CARD, fg=GOLD,
                 font=fnt(11, "bold")).pack(side="left")
        self.lbl_source = tk.Label(rec_head, text="", bg=CARD, fg=MUTED,
                                   font=fnt(8))
        self.lbl_source.pack(side="right")

        self.rec_canvas = tk.Canvas(rec_card, bg=CARD, highlightthickness=0,
                                    height=sp(300))
        scroll = ttk.Scrollbar(rec_card, orient="vertical",
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
                             padx=(sp(10), 0), pady=(0, sp(8)))
        scroll.pack(side="right", fill="y", pady=(0, sp(8)))
        self.rec_canvas.bind_all("<MouseWheel>",
                                 lambda e: self.rec_canvas.yview_scroll(
                                     int(-e.delta / 120), "units"))
        self.rec_canvas.bind("<Configure>", self._on_rec_canvas_configure)

        self.lbl_hint = tk.Label(self.rec_inner, text="等待对位数据…", bg=CARD,
                                 fg=MUTED, font=fnt(10))
        self.lbl_hint.pack(pady=sp(30))

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
                target = self.engine.target_enemy(snap)
                manual = bool(self.engine.manual_enemy_id)
                lane = self.engine.manual_lane if manual else snap.my_lane
                key = (target, lane or "auto") if target else None

                if target and key != self._rec_key:
                    self._rec_key = key
                    self._rec_gen += 1
                    gen = self._rec_gen
                    # 1) 离线表立即出首帧（零等待）
                    off_recs, off_src = self.engine.recommender.recommend_offline(
                        target, lane or "top")
                    if off_recs:
                        self._last_recs = (off_recs, off_src)
                    else:
                        self._last_recs = ([], "")
                    self.q.put(("computing", None))
                    # 2) 在线源后台并行竞速，拉到后替换
                    def on_online(recs, src, _gen=gen, _key=key):
                        if _gen == self._rec_gen and _key == self._rec_key:
                            self.q.put(("recs_online", (recs, src)))
                    self.engine.recommender.recommend_async(
                        target, lane or "top", on_online=on_online, quick_timeout=8.0)
                elif not target:
                    self._rec_key = None
                    self._last_recs = ([], "")

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
                    ens[c.champion_id] = self.engine.db.en_of(c.champion_id)
                got_new = False
                for cid, en in ens.items():
                    if download_avatar(cid, en, self.engine.db.version or "",
                                       self.cfg.http_timeout):
                        got_new = True
                self.q.put(("state", (snap, self._last_recs, manual, lane)))
                if got_new:
                    self.q.put(("avatars", None))
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
        except queue.Empty:
            pass
        self.root.after(200, self._drain_queue)

    # ---------------- 渲染 ----------------
    def _render(self):
        snap = self._snap
        recs, source = self._last_recs
        target = self.engine.target_enemy(snap) if snap else 0

        color, phase_txt = PHASE_DOT.get(snap.phase if snap else "starting",
                                         PHASE_DOT["starting"])
        self.dot.itemconfigure(self.dot_id, fill=color)
        self.lbl_status.configure(text=phase_txt)

        if snap and snap.my_champ_id:
            info = self.engine.db.by_champion_id(snap.my_champ_id)
            lane_txt = f"｜{LANE_CN.get(snap.my_lane, '')}" if snap.my_lane else ""
            self.lbl_my.configure(text=f"{info['name']}（{info['en']}）{lane_txt}",
                                  fg=TEXT)
        else:
            self.lbl_my.configure(text="等待识别…", fg=MUTED)

        if target:
            info = self.engine.db.by_champion_id(target)
            self.lbl_target_name.configure(text=info["name"])
            qlane = self._lane or self._guess_lane(target)
            self.lbl_target_sub.configure(
                text=f"{info['en']}｜查询分路：{LANE_CN.get(qlane, '自动')}", fg=MUTED)
            self.lbl_badge.configure(text="● 手动选择" if self._manual else "● 自动识别",
                                     fg=GOLD if self._manual else TEAL)
            self._set_target_avatar(target, info["en"])
        else:
            self.lbl_target_name.configure(text="—")
            self.lbl_target_sub.configure(text="进入选将或游戏后自动识别，也可手动查询",
                                          fg=MUTED)
            self.lbl_badge.configure(text="")
            self.lbl_target_avatar.configure(
                image=self._placeholder(76, CARD2), text="?", fg=MUTED,
                compound="center", font=fnt(16, "bold"),
                width=sp(76), height=sp(76))

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
        img = self._get_photo(cid, en, 76)
        if img:
            self.lbl_target_avatar.configure(image=img, text="",
                                             width=sp(76), height=sp(76))
            self.lbl_target_avatar.image = img
        else:
            self.lbl_target_avatar.configure(
                image=self._placeholder(76, CARD2), text="?", fg=MUTED,
                compound="center", font=fnt(16, "bold"),
                width=sp(76), height=sp(76))

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
            self.lbl_source.configure(text=source or "等待数据")
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

            pct = tk.Label(row, text=f"{c.my_winrate:.1f}%", bg=row_bg,
                           fg=self._wr_color(c.my_winrate),
                           font=fnt(12, "bold"))
            pct.pack(side="right", padx=sp(10))

            bar_w, bar_h = sp(92), sp(10)
            bar = tk.Canvas(row, width=bar_w, height=bar_h, bg=row_bg,
                            highlightthickness=0)
            bar.pack(side="right", padx=sp(4))
            bar.create_rectangle(0, sp(3), bar_w, sp(9), fill=BORDER, outline="")
            frac = max(0.05, min(1.0, (c.my_winrate - 46.0) / 10.0))
            bar.create_rectangle(0, sp(3), int(bar_w * frac), sp(9),
                                 fill=self._wr_color(c.my_winrate), outline="")
            self._row_widgets.extend([row, rk, avatar, name_box, pct, bar]
                                     + name_box.winfo_children())

        tag = "在线数据" if source in ("lolalytics", "blitz") else "离线表"
        self.lbl_source.configure(text=f"数据源：{tag}　·　按克制幅度排序（胜率为我方对位胜率）")

    @staticmethod
    def _wr_color(wr: float) -> str:
        if wr >= 53:
            return GREEN
        if wr >= 51:
            return TEAL
        if wr >= 50:
            return GOLD_DIM
        return MUTED

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

    # ---------------- 内嵌手动查询栏 ----------------
    SEARCH_LANE_OPTS = [("自动", ""), ("上单", "top"), ("打野", "jungle"),
                        ("中单", "middle"), ("下路", "bottom"), ("辅助", "support")]

    def _build_search_bar(self):
        card = self._mk_card(self.root)
        card.pack(fill="x", padx=sp(12), pady=(0, sp(8)))

        self.engine.db.load()
        items = []
        for info in self.engine.db.by_id.values():
            alias = info.get("alias", "")
            title = info["title"] if info["title"] != info["name"] else ""
            extra = alias.split()[0] if alias else title
            head = info["name"] + (f"（{extra}）" if extra else "")
            items.append(f"{head}｜{info['en']}")
        self._champ_names = sorted(set(items))

        tk.Label(card, text="手动查询：输入或选择敌方英雄后回车", bg=CARD,
                 fg=GOLD, font=fnt(9, "bold")).pack(anchor="w",
                                                    padx=sp(12), pady=(sp(8), sp(3)))

        row1 = tk.Frame(card, bg=CARD)
        row1.pack(fill="x", padx=sp(10))
        self.var_search = tk.StringVar()
        self.combo_search = ttk.Combobox(row1, textvariable=self.var_search,
                                         values=self._champ_names, font=fnt(10))
        self.combo_search.pack(side="left", fill="x", expand=True)
        self.combo_search.bind("<KeyRelease>", self._filter_champs)
        self.combo_search.bind("<Return>", lambda e: self._do_search())
        self._mk_button(row1, "查询", self._do_search, TEAL).pack(
            side="left", padx=(sp(8), 0))

        row2 = tk.Frame(card, bg=CARD)
        row2.pack(fill="x", padx=sp(10), pady=(sp(6), sp(8)))
        tk.Label(row2, text="分路", bg=CARD, fg=MUTED,
                 font=fnt(9)).pack(side="left", padx=(0, sp(4)))
        self.var_search_lane = tk.StringVar(value="自动")
        self.combo_lane = ttk.Combobox(
            row2, textvariable=self.var_search_lane, state="readonly",
            values=[t for t, _ in self.SEARCH_LANE_OPTS], font=fnt(9), width=6)
        self.combo_lane.pack(side="left")

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
