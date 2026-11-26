from enum import StrEnum


class ArchiveState(StrEnum):
    # 冷档副本正在建立（可能在任意一步中断，重试沿用同一 run_id 行）。
    COPYING = "copying"
    COPIED = "copied"
    VERIFIED = "verified"
    # 回执已一次性切换到冷档，旧库批次已在同一事务中清除。
    ACTIVE = "active"
    # 独立读回校验不一致，旧库批次保留，禁止清理。
    MISMATCH = "mismatch"
    FAILED = "failed"


class ArchiveStatus(StrEnum):
    ARCHIVED = "archived"
    MIGRATING = "migrating"
    BLOCKED = "blocked"
