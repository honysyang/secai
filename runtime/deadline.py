"""比赛硬总时限常量。

由环境变量 TASK_DEADLINE_TS 注入 Unix 时间戳秒；主循环在接近截止时间时
停止派发新题目，留出收尾余量。
"""
from __future__ import annotations

import os

# 比赛硬总时限（Unix 时间戳秒）。空=不限。
TASK_DEADLINE_TS = os.getenv("TASK_DEADLINE_TS", "").strip()
DEADLINE_SAFE_MARGIN = 60  # 比赛结束前 N 秒判停，留出收尾时间
