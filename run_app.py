# -*- coding: utf-8 -*-
"""PyInstaller 打包入口：启动 GUI 桌面窗口。

打包命令见 build_exe.bat。
"""
import os
import sys

# --windowed（无控制台）模式下 stdout/stderr 可能为 None，
# 业务代码中的 print 不能因此崩溃，重定向到空设备。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")

from lolcp.__main__ import main

if __name__ == "__main__":
    main()
