"""安全 CLI 工具注册表：从 YAML 定义加载本地安全工具，拼命令并执行。

来源：sec-agent-v2 的 tools/*.yaml（name / command / description / parameters）。
每个 YAML 描述一个本地 CLI 工具如何调用；本模块负责：
  1. 初始化阶段扫描 YAML 目录；
  2. 用 shutil.which 检测本机是否已安装对应 command，只保留已安装的；
  3. 按 parameters 的 flag / combined / positional / template 拼出命令行；
  4. subprocess 执行并返回 stdout/stderr；
  5. 提供可选的安装入口（apt / pip，装不上则跳过）。

用法：
    python sec_tools.py list             # 列出已安装可用工具
    python sec_tools.py missing          # 列出缺失工具
    python sec_tools.py install          # 尝试安装缺失工具（失败跳过）
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_TOOLS_DIR = str(Path(__file__).parent.parent / "tools")
_env_tools_dir = os.getenv("TOOLS_DIR", "")
TOOLS_DIR = _env_tools_dir if _env_tools_dir and Path(_env_tools_dir).exists() else DEFAULT_TOOLS_DIR

MAX_OUTPUT = 8000          # stdout 截断
DEFAULT_TIMEOUT = 300      # 默认单工具超时（秒）

# 缺失工具 → (包管理器, 包名)；未列出的按工具名用 apt 尝试
INSTALL_MAP = {
    "nmap": ("apt", "nmap"),
    "sqlmap": ("apt", "sqlmap"),
    "nuclei": ("apt", "nuclei"),
    "ffuf": ("apt", "ffuf"),
    "gobuster": ("apt", "gobuster"),
    "dirsearch": ("apt", "dirsearch"),
    "nikto": ("apt", "nikto"),
    "hydra": ("apt", "hydra"),
    "john": ("apt", "john"),
    "hashcat": ("apt", "hashcat"),
    "wpscan": ("apt", "wpscan"),
    "masscan": ("apt", "masscan"),
    "subfinder": ("apt", "subfinder"),
    "amass": ("apt", "amass"),
    "metasploit": ("apt", "metasploit-framework"),
    "radare2": ("apt", "radare2"),
    "gdb": ("apt", "gdb"),
    "pwntools": ("pip", "pwntools"),
    "angr": ("pip", "angr"),
    "impacket": ("pip", "impacket"),
    "ropper": ("pip", "ropper"),
    "slither": ("pip", "slither-analyzer"),
    "mythril": ("pip", "mythril"),
}


@dataclass
class ToolSpec:
    name: str
    command: str
    enabled: bool
    short_description: str = ""
    description: str = ""
    args: List[str] = field(default_factory=list)
    parameters: List[Dict[str, Any]] = field(default_factory=list)


@lru_cache(maxsize=1)
def load_specs(tools_dir: str = TOOLS_DIR) -> Dict[str, ToolSpec]:
    """扫描 YAML 目录，返回 {工具名: ToolSpec}（跳过非法/未启用项）。"""
    specs: Dict[str, ToolSpec] = {}
    root = Path(tools_dir)
    if not root.exists():
        return specs
    for p in sorted(root.glob("*.y*ml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        command = str(raw.get("command") or "").strip()
        if not name or not command:
            continue
        enabled = bool(raw.get("enabled", True))
        spec = ToolSpec(
            name=name,
            command=command,
            enabled=enabled,
            short_description=str(raw.get("short_description") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            args=[str(a) for a in (raw.get("args") or [])],
            parameters=[dict(x) for x in (raw.get("parameters") or []) if isinstance(x, dict)],
        )
        specs[name] = spec
    return specs


def is_installed(spec: ToolSpec) -> bool:
    return shutil.which(spec.command) is not None


@lru_cache(maxsize=1)
def available_tools(tools_dir: str = TOOLS_DIR) -> Dict[str, ToolSpec]:
    """只返回 enabled 且本机已安装的工具。"""
    return {n: s for n, s in load_specs(tools_dir).items() if s.enabled and is_installed(s)}


def missing_tools(tools_dir: str = TOOLS_DIR) -> List[str]:
    specs = load_specs(tools_dir)
    return [n for n, s in specs.items() if s.enabled and not is_installed(s)]


def get_spec(name: str) -> Optional[ToolSpec]:
    return load_specs().get(name)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def build_command(spec: ToolSpec, args: Dict[str, Any]) -> List[str]:
    """按 YAML parameters 拼出命令行。"""
    cmd: List[str] = [spec.command] + list(spec.args or [])
    pos0: List[str] = []
    flags: List[str] = []
    pos_rest: List[tuple[int, str]] = []
    additional: List[str] = []

    for p in spec.parameters:
        name = str(p.get("name") or "")
        value = args.get(name)
        if value is None:
            value = p.get("default")
        if value is None:
            continue

        if name == "additional_args":
            try:
                additional = shlex.split(str(value))
            except ValueError:
                additional = str(value).split()
            continue

        fmt = str(p.get("format") or "flag")
        if fmt == "positional":
            pos = int(p.get("position") or 99)
            if pos == 0:
                pos0.append(str(value))
            else:
                pos_rest.append((pos, str(value)))
        elif fmt == "combined":
            flag = str(p.get("flag") or "")
            flags.append(f"{flag}={value}")
        elif fmt == "template":
            tpl = str(p.get("template") or "{value}")
            flags.append(tpl.replace("{value}", str(value)))
        else:  # flag
            flag = str(p.get("flag") or "")
            if not flag:
                pos_rest.append((int(p.get("position") or 99), str(value)))
            elif p.get("type") == "bool":
                if _to_bool(value):
                    flags.append(flag)
            else:
                flags.extend([flag, str(value)])

    cmd.extend(pos0)
    cmd.extend(flags)
    cmd.extend(v for _, v in sorted(pos_rest, key=lambda x: x[0]))
    cmd.extend(additional)
    return cmd


def execute(name: str, args: Dict[str, Any], *, timeout: int = DEFAULT_TIMEOUT,
            workdir: Optional[str] = None) -> Dict[str, Any]:
    """执行一个工具，返回 {command, rc, stdout, stderr} 或 {error}。"""
    spec = available_tools().get(name)
    if spec is None:
        if name in missing_tools():
            return {"error": f"工具 '{name}' 未安装（可用 `python sec_tools.py missing` 查看）"}
        return {"error": f"工具 '{name}' 不存在或未启用"}
    cmd = build_command(spec, args or {})
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=min(timeout, 600), cwd=workdir)
        return {
            "command": " ".join(cmd),
            "rc": p.returncode,
            "stdout": p.stdout[:MAX_OUTPUT],
            "stderr": p.stderr[:2000],
        }
    except subprocess.TimeoutExpired:
        return {"command": " ".join(cmd), "error": f"执行超时（{timeout}s）"}
    except Exception as e:
        return {"command": " ".join(cmd), "error": str(e)[:300]}


def install_missing(dry_run: bool = True) -> Dict[str, Any]:
    """尝试安装缺失工具（apt / pip，失败跳过）。dry_run=True 只返回安装命令不执行。"""
    results: Dict[str, Any] = {}
    for name in missing_tools():
        manager, pkg = INSTALL_MAP.get(name, ("apt", name))
        if manager == "pip":
            cmd = ["pip", "install", "-q", pkg]
        else:
            cmd = ["apt-get", "install", "-y", pkg]
        if dry_run:
            results[name] = {"plan": " ".join(cmd)}
            continue
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
            results[name] = {"installed": is_installed(load_specs()[name])}
        except Exception as e:
            results[name] = {"error": str(e)[:200]}
    return results


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args or args[0] in {"list", "ls"}:
        tools = available_tools()
        print(f"可用工具 {len(tools)} 个：")
        for n, s in tools.items():
            print(f"  {n}\t{s.short_description or s.description[:60]}")
    elif args[0] == "missing":
        miss = missing_tools()
        print(f"缺失工具 {len(miss)} 个：")
        for n in miss:
            print(f"  {n}")
    elif args[0] == "install":
        dry = "--yes" not in args
        print("DRY-RUN" if dry else "正在安装...")
        for name, res in install_missing(dry_run=dry).items():
            print(f"  {name}: {json.dumps(res, ensure_ascii=False)}")
    else:
        print("用法：")
        print("  python sec_tools.py list      # 可用工具")
        print("  python sec_tools.py missing   # 缺失工具")
        print("  python sec_tools.py install [--yes]  # 安装缺失（--yes 实际执行）")
