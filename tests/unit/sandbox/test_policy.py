"""sandbox/policy.py 单测 —— 命令分级词表 + Policy 档位（R3 验收：词表 ≥6 条）。"""

import pytest

from sandbox.policy import (
    DANGER,
    DANGER_REGEXES,
    DANGER_SUBSTRINGS,
    READ_ONLY,
    READ_ONLY_COMMANDS,
    WORKSPACE_WRITE,
    Policy,
    classify_command,
)


def test_danger_wordlist_has_at_least_six_entries() -> None:
    """R3 验收：policy 词表 ≥6 条（正则 + 子串合计）。"""
    total = len(DANGER_REGEXES) + len(DANGER_SUBSTRINGS)
    assert total >= 6
    assert len(READ_ONLY_COMMANDS) >= 10  # 只读白名单兜底


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "rm -fr /",
        "rm -Rf /",
        "rm -rf /*",
        "rm -rf / ; echo pwned",
        "sudo rm -rf /",
        "rm --no-preserve-root -rf /",
        "mkfs.ext4 /dev/sdb1",
        "mkfs /dev/sdc",
        "dd if=/dev/zero of=/dev/sda bs=1M count=10",
        "chmod -R 777 /",
        "chown -R root:root /",
        "shutdown -h now",
        "reboot",
        ":(){ :|:& };:",
        "echo x > /dev/sda",
    ],
)
def test_classify_danger(cmd: str) -> None:
    assert classify_command(cmd) == DANGER


@pytest.mark.parametrize(
    "cmd",
    [
        "cat /etc/passwd",
        "ls -la /",
        "head -5 /etc/hostname",
        "find / -maxdepth 2 -name '*.conf'",
        "whoami",
    ],
)
def test_classify_read_only(cmd: str) -> None:
    assert classify_command(cmd) == READ_ONLY


@pytest.mark.parametrize(
    "cmd",
    [
        "python3 exploit.py",
        "touch /tmp/probe.txt",
        "curl http://10.0.0.5/",
        "nmap -sV 10.0.0.5",
        "rm -rf /etc/hostname",  # 非根目录级 rm → 不进拦截清单（沙箱 ro 兜底）
    ],
)
def test_classify_workspace_write(cmd: str) -> None:
    assert classify_command(cmd) == WORKSPACE_WRITE


def test_classify_empty_command_read_only() -> None:
    assert classify_command("") == READ_ONLY


def test_policy_default_level() -> None:
    assert Policy().level == WORKSPACE_WRITE
    assert Policy().allows_write()


def test_policy_read_only_level_rejects_write_tier() -> None:
    p = Policy(level=READ_ONLY)
    assert not p.allows_write()
    assert p.allows_tier(READ_ONLY)
    assert not p.allows_tier(WORKSPACE_WRITE)
    assert not p.allows_tier(DANGER)


def test_policy_danger_level_only_read_only_allowed() -> None:
    p = Policy(level=DANGER)
    assert not p.allows_write()
    assert p.allows_tier(READ_ONLY)
    assert not p.allows_tier(WORKSPACE_WRITE)


def test_policy_workspace_write_allows_write_tier() -> None:
    p = Policy(level=WORKSPACE_WRITE)
    assert p.allows_tier(WORKSPACE_WRITE)
    assert p.allows_tier(READ_ONLY)


def test_policy_unknown_level_raises() -> None:
    with pytest.raises(ValueError):
        Policy(level="god_mode")


def test_classify_list_form() -> None:
    assert classify_command(["rm", "-rf", "/"]) == DANGER
    assert classify_command(["cat", "/etc/hostname"]) == READ_ONLY
