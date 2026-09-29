"""命令行入口。

用法：
  python -m lolcp              # 启动桌面悬浮窗（默认）
  python -m lolcp --cli        # 命令行模式（无 GUI，适合调试/服务器）
  python -m lolcp --test-data  # 用假数据测试推荐流程（不需要游戏）
  python -m lolcp --enemy 266 --lane top   # 直接查某个英雄的 counter
"""
from __future__ import annotations

import argparse
import sys
import time

from .config import Config
from .engine import Engine, LANE_CN


def _print_recs(enemy_id: int, lane: str, engine: Engine):
    """在命令行打印对指定敌方英雄的克制推荐列表（无界面时的备用展示）。"""
    ename = engine.db.name_of(enemy_id)
    recs, source = engine.recommend(enemy_id, lane)
    print(f"\n对位英雄：{ename}（分路：{LANE_CN.get(lane, lane or '自动')}）  数据源：{source or '无'}")
    if not recs:
        print("  未获取到对位数据（网络不可用且离线表无此英雄）。")
        return
    print("-" * 46)
    for i, c in enumerate(recs, 1):
        print(f"{i:>2}. {c.name:<12} 对位胜率 {c.my_winrate:>5.1f}%")
    print("-" * 46)


def run_cli(cfg: Config):
    """命令行交互模式：列出敌方英雄，选择对位后打印克制推荐。"""
    engine = Engine(cfg)
    print("LOL 对位 Counter 推荐（命令行模式）")
    print("检测中… 进入选将阶段或游戏后自动刷新。Ctrl+C 退出。\n")
    last_key = None
    try:
        while True:
            snap = engine.poll()
            target = engine.target_enemy(snap)
            key = (snap.phase, target, snap.my_lane,
                   tuple(e.champion_id for e in snap.enemies))
            if key != last_key:
                print(f"[{time.strftime('%H:%M:%S')}] 状态：{snap.phase_cn}"
                      f"{'｜分路：' + LANE_CN.get(snap.my_lane, '') if snap.my_lane else ''}")
                if snap.enemies:
                    print("  敌方英雄：" + "、".join(
                        f"{e.name}({e.lane_cn or '位置未定'})" for e in snap.enemies))
                if target:
                    _print_recs(target, snap.my_lane, engine)
                last_key = key
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        print("\n已退出。")


def run_test(cfg: Config):
    """自检模式：依次测试英雄库加载、LCU 与 live 客户端连接并打印结果。"""
    engine = Engine(cfg)
    print("== 自测模式：不需要游戏客户端 ==")
    for eid, lane in [(266, "top"), (84, "middle"), (238, "middle"),
                      (121, "jungle"), (222, "bottom"), (412, "support"),
                      (86, "top")]:
        _print_recs(eid, lane, engine)


def run_query(cfg: Config, enemy_en: str, lane: str):
    """单英雄查询模式：直接查询并打印指定英雄的克制推荐。"""
    engine = Engine(cfg)
    cid = engine.db.id_by_en(enemy_en)
    if not cid:
        print(f"找不到英雄：{enemy_en}（请用英文名，如 Darius / Jinx / Thresh）")
        sys.exit(1)
    _print_recs(cid, lane, engine)


def main():
    """程序命令行入口：解析参数并分发到 GUI / CLI / 自检 / 单查询模式。"""
    ap = argparse.ArgumentParser(description="LOL 对位识别与 Counter 推荐")
    ap.add_argument("--cli", action="store_true", help="命令行模式（无 GUI）")
    ap.add_argument("--gui", action="store_true", help="桌面悬浮窗模式（默认）")
    ap.add_argument("--test-data", action="store_true", help="离线自测")
    ap.add_argument("--enemy", help="直接查询：敌方英雄英文名，如 Darius")
    ap.add_argument("--lane", default="top", choices=["top", "jungle", "middle", "bottom", "support"],
                    help="分路（配合 --enemy 使用）")
    args = ap.parse_args()

    cfg = Config.load()

    if args.test_data:
        run_test(cfg)
    elif args.enemy:
        run_query(cfg, args.enemy, args.lane)
    elif args.cli:
        run_cli(cfg)
    else:
        try:
            from .app import CounterPickerApp
            CounterPickerApp(cfg).run()
        except Exception as e:
            print(f"GUI 启动失败（{e}），改用命令行模式。")
            run_cli(cfg)


if __name__ == "__main__":
    main()
