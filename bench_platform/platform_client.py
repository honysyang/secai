"""平台客户端：对齐 TSec SDK 语义的零依赖实现。全系统只有这里懂平台协议。

R4 H12 平台客户端收口：模块级 get_platform_client() 是本进程唯一的
PlatformClient 构造点（demo_tools._platform / platform_tools._client /
app.main 的重复构造全部消除），凭证来源统一收敛到 adapters.config（.env）。
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from adapters.config import BENCHMARK_BASE_URL, BENCHMARK_TOKEN

VPN_CHECK_URL = os.getenv("TSEC_VPN_CHECK_URL", "http://10.0.100.58")


class VpnCheckError(Exception):
    def __init__(self, reason: str = "network_error"):
        super().__init__("VPN检测未通过,请检查靶场VPN网络配置")
        self.reason = reason

class TaskNotFound(Exception): ...        # 404 token 无效：停止报告
class TaskEnded(Exception): ...           # 409 invalid_state 非 max active：全局停
class ContainerBusy(Exception): ...       # 409 含 max active：close 再试
class ResourceUnavailable(Exception): ... # 503：短暂重试


def _err_code(resp) -> str:
    try:
        return str(resp.json().get("code", ""))
    except Exception:
        return ""


def _err_msg(resp) -> str:
    try:
        return str(resp.json().get("message", ""))
    except Exception:
        return ""


class PlatformClient:
    def __init__(self, base_url: str, token: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.headers = {"BENCHMARK_TOKEN": token}
        self.timeout = timeout

    # ---- VPN 强制预检：开战前的第一道闸门 ----
    def check_vpn(self) -> dict[str, Any]:
        try:
            r = requests.get(VPN_CHECK_URL, timeout=8)
        except Exception:
            raise VpnCheckError("network_error")
        if r.status_code != 200:
            raise VpnCheckError("bad_status")
        try:
            data = r.json()
        except Exception:
            raise VpnCheckError("bad_body")
        if data.get("status") != "ok":
            raise VpnCheckError("status_not_ok")
        return data

    def _check_common(self, resp) -> None:
        code = _err_code(resp)
        if code == "task_not_found":
            raise TaskNotFound(_err_msg(resp) or "token 无效或缺失")
        if code == "invalid_state":
            msg = _err_msg(resp)
            if "max active" in msg:
                raise ContainerBusy(msg)
            raise TaskEnded(msg or "任务已结束")

    # ---- 五个平台接口 ----
    def list_challenges(self) -> list[dict[str, Any]]:
        r = requests.get(f"{self.base_url}/openapi/v1/challenges",
                         headers=self.headers, timeout=self.timeout)
        self._check_common(r)
        r.raise_for_status()
        return r.json()

    def start_challenge(self, code: str) -> list[str]:
        r = requests.post(f"{self.base_url}/openapi/v1/challenges/start",
                          params={"unique_code": code},
                          headers=self.headers, timeout=self.timeout + 5)
        self._check_common(r)
        if _err_code(r) == "resource_unavailable" or r.status_code == 503:
            raise ResourceUnavailable(_err_msg(r))
        r.raise_for_status()
        addr = r.json().get("container_addr") or []
        if isinstance(addr, str):
            addr = [addr]
        return [str(a) for a in addr]

    def get_hint(self, code: str) -> str:
        try:
            r = requests.get(f"{self.base_url}/openapi/v1/challenges/hint",
                             params={"unique_code": code},
                             headers=self.headers, timeout=self.timeout)
            if r.status_code != 200:
                return ""      # 通关后看 hint 返回 409：跳过
            return r.json().get("hint") or ""
        except (TaskNotFound, TaskEnded):
            raise
        except Exception:
            return ""

    def submit_flag(self, code: str, flag: str) -> dict[str, Any]:
        """大小写变体 + 429 退避 + duplicate 幂等。"""
        variants = [flag] + (["FLAG" + flag[4:]] if flag.startswith("flag") else [])
        last: dict[str, Any] = {}
        for v in variants:
            for attempt in range(3):
                try:
                    r = requests.post(f"{self.base_url}/openapi/v1/challenges/submit",
                                      json={"unique_code": code, "flag": v},
                                      headers=self.headers, timeout=self.timeout)
                    if _err_code(r) == "duplicate":
                        return {"ok": True, "correct": None, "duplicate": True,
                                "note": "duplicate：已计分，跳过", "flag": v}
                    self._check_common(r)
                    if r.status_code == 429:
                        time.sleep(2 * (attempt + 1))
                        continue
                    if "json" in r.headers.get("Content-Type", ""):
                        last = r.json()
                    break
                except (TaskNotFound, TaskEnded):
                    raise
                except Exception as e:
                    last = {"error": str(e)[:200]}
                    time.sleep(1)
            if last.get("correct"):
                return {"ok": True, "correct": True, "flag": v,
                        "awarded": last.get("awarded"),
                        "cumulative_score": last.get("cumulative_score"),
                        "correct_flag_count": last.get("correct_flag_count"),
                        "total_flag_count": last.get("total_flag_count"),
                        "matched_flag_index": last.get("matched_flag_index")}
        return {"ok": True, "correct": False, "flag": flag,
                "note": "平台判定错误或题目不匹配", "raw": last}

    def close_challenge(self, code: str) -> bool:
        try:
            r = requests.post(f"{self.base_url}/openapi/v1/challenges/close",
                              params={"unique_code": code},
                              headers=self.headers, timeout=self.timeout)
            self._check_common(r)
            return bool(r.json().get("closed"))
        except (TaskNotFound, TaskEnded):
            raise
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 模块级平台客户端单例（R4 H12：消除各处的重复构造）
# 全进程唯一构造点；app.main / platform_tools / profiles.ctf_legacy 一律经此获取。
# ---------------------------------------------------------------------------
_platform_client: "PlatformClient | None" = None


def get_platform_client() -> "PlatformClient":
    """按 .env 平台凭证构造模块级单例；重复调用返回同一实例。"""
    global _platform_client
    if _platform_client is None:
        _platform_client = PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)
    return _platform_client


def platform_configured() -> bool:
    """平台凭证是否已配置（BENCHMARK_BASE_URL 与 BENCHMARK_TOKEN 均非空）。"""
    return bool(BENCHMARK_BASE_URL and BENCHMARK_TOKEN)
