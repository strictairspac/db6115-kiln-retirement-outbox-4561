from sqlalchemy import JSON, BigInteger, Column, Integer, String, Table

from .base import metadata

# 冷档：每个原批次号（runs.id）至多一行，run_id 唯一约束保证迁档重试不产生副本。
# case_id 不建外键——资料清理后冷档回执仍须独立留存、按原批次号可查。
cold_archives = Table(
    "cold_archives", metadata,
    # id 直接取原批次号（runs.id），API 中 id/run_id/batch_no 都指向它。
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("run_id", Integer, unique=True, nullable=False, index=True),
    Column("batch_no", String(48), unique=True, nullable=False, index=True),
    Column("case_id", Integer, nullable=False, index=True),
    Column("case_code", String(48), nullable=True),
    Column("case_title", String(120), nullable=True),
    # copying -> copied -> verified -> active（一次性切换并清旧库）；mismatch 为校验未过。
    Column("state", String(24), nullable=False, default="active", index=True),
    Column("status", String(24), nullable=False, default="archived", index=True),
    Column("storage", String(16), nullable=False, default="cold"),
    Column("location", String(16), nullable=False, default="cold"),
    # 三件必须独立读回并对得上的东西：回执内容、签字、校验摘要。
    Column("receipt", JSON, nullable=False),
    Column("result", JSON, nullable=False),
    Column("signature", JSON(none_as_null=True), nullable=True),
    Column("review", JSON(none_as_null=True), nullable=True),
    Column("digest", String(64), nullable=True),
    Column("receipt_digest", String(64), nullable=True),
    Column("signature_digest", String(64), nullable=True),
    Column("run_snapshot", JSON, nullable=True),
    Column("checks", JSON(none_as_null=True), nullable=True),
    Column("file_path", String(240), nullable=True),
    Column("copies", Integer, nullable=False, default=1),
    Column("attempts", Integer, nullable=False, default=0),
    Column("actor", String(48), nullable=False, default="system"),
    Column("source", String(16), nullable=False, default="hot"),
    Column("created_at", BigInteger, nullable=False),
    Column("verified_at", BigInteger, nullable=True),
    Column("archived_at", BigInteger, nullable=True),
    Column("sealed_at", BigInteger, nullable=True),
    Column("updated_at", BigInteger, nullable=True),
)
