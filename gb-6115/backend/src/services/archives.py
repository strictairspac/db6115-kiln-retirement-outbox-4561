"""冷档迁档服务。

不变量（对应验收口径）：

1. 只有 ``done`` 且已有复核签字的批次可迁冷档。
2. 冷档以 ``run_id`` 唯一：任何阶段中断后重试都在同一行上续写，绝不新增副本；
   冷档文件副本使用确定文件名 ``batch-<run_id>.json``，重写不产生第二份。
3. 冷档内容必须用**独立的一次读回**核对：回执正文、签字、校验摘要三者与旧库一致，
   才允许进入切换/清理；不一致时旧库批次原样保留。
4. 切换是单个数据库事务：冷档置 active 与删除旧库 run/review 一起提交，
   不存在“两边各取一半”的回执；整个过程中查询要么读到旧库、要么读到完整冷档。
5. 演练资料仅当其下已无任何旧库批次行时才可清理——最后一个引用批次必已完成迁档；
   迁档期间资料未删，可继续对其发起新批次（新批次会重新阻止资料清理）。
"""
import json
import logging
import os
from time import time

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.resources import engine
from src.config.settings import COLD_ARCHIVE_DIR
from src.constants.archive_state import ArchiveState, ArchiveStatus
from src.constants.job_state import JobState
from src.domain.archive import (
    compare_parts,
    normalize_signature,
    normalize_snapshot,
    same_batch_number,
    signature_digest,
)
from src.models.archives import cold_archives
from src.models.cases import cases
from src.models.reviews import reviews
from src.models.runs import runs
from src.services import audit
from src.utils.serial import canonical_json

logger = logging.getLogger(__name__)


class MigrationInterrupted(Exception):
    """演练用：在指定阶段制造一次“迁档中断”。"""

    def __init__(self, run_id, stage):
        super().__init__(f"archive migration interrupted at {stage}")
        self.run_id = run_id
        self.stage = stage


# --------------------------------------------------------------------------- #
# 序列化与查找
# --------------------------------------------------------------------------- #

def _now():
    return int(time())


def serialize(row):
    if row is None:
        return None
    data = dict(row)
    run_id = data.get("run_id") or data.get("id")
    # 对外统一：id 即原批次号。
    data["id"] = run_id
    data["run_id"] = run_id
    data.setdefault("batch_id", run_id)
    data.setdefault("batch_no", str(run_id))
    data.setdefault("archived", data.get("state") == ArchiveState.ACTIVE)
    data.setdefault("cold", True)
    data.setdefault("receipt_ready", data.get("state") == ArchiveState.ACTIVE)
    return data


def find_by_identifier(db, identifier):
    """按原批次号（数字或 BATCH-12 / #12 字符串）查冷档行，不限状态。"""
    if identifier is None:
        return None
    row = None
    if isinstance(identifier, int) or str(identifier).strip().isdigit():
        run_id = int(identifier)
        row = db.execute(
            select(cold_archives).where(cold_archives.c.run_id == run_id)
        ).mappings().first()
    if row is None:
        candidate = str(identifier).strip()
        row = db.execute(
            select(cold_archives).where(cold_archives.c.batch_no == candidate)
        ).mappings().first()
    if row is None:
        for item in db.execute(select(cold_archives)).mappings():
            if same_batch_number(item["run_id"], identifier):
                row = item
                break
    return serialize(row) if row else None


def get_archive(db, run_id):
    entry = find_by_identifier(db, run_id)
    if entry is None:
        raise HTTPException(404, "冷档中没有该批次")
    return entry


def list_archives(db, limit=100):
    rows = db.execute(
        select(cold_archives).order_by(cold_archives.c.run_id.desc()).limit(limit)
    ).mappings()
    return [serialize(row) for row in rows]


# --------------------------------------------------------------------------- #
# 旧库装载
# --------------------------------------------------------------------------- #

def load_hot_bundle(db, run_id):
    # 单条语句把批次、资料名和签字一起读出，保证“一次性切换”期间不会读到
    # 批次尚在、签字已删之类的跨事务碎片。
    row = db.execute(
        select(
            runs,
            cases.c.code.label("case_code"),
            cases.c.title.label("case_title"),
            reviews.c.run_id.label("review_run_id"),
            reviews.c.actor.label("review_actor"),
            reviews.c.note.label("review_note"),
            reviews.c.created_at.label("review_created_at"),
        )
        .select_from(runs)
        .outerjoin(cases, cases.c.id == runs.c.case_id)
        .outerjoin(reviews, reviews.c.run_id == runs.c.id)
        .where(runs.c.id == run_id)
    ).mappings().first()
    if row is None:
        return None
    row = dict(row)
    review = None
    if row.get("review_run_id") is not None:
        review = {
            "run_id": row["review_run_id"],
            "actor": row["review_actor"],
            "note": row["review_note"],
            "created_at": row["review_created_at"],
        }
    return {"run": row, "review": review}


# --------------------------------------------------------------------------- #
# 可选的冷档文件副本（共享产物卷），确定文件名 -> 重试覆盖同一文件
# --------------------------------------------------------------------------- #

def _file_envelope(entry):
    return {
        "run_id": entry["run_id"],
        "batch_no": entry["batch_no"],
        "case_id": entry["case_id"],
        "digest": entry["digest"],
        "receipt": entry["receipt"],
        "signature": entry["signature"],
        "archived_at": entry.get("archived_at"),
    }


def _file_path(run_id):
    if not COLD_ARCHIVE_DIR:
        return None
    return os.path.join(COLD_ARCHIVE_DIR, f"batch-{run_id}.json")


def write_cold_file(entry):
    path = _file_path(entry["run_id"])
    if path is None:
        return None
    os.makedirs(COLD_ARCHIVE_DIR, exist_ok=True)
    # 唯一临时名：同进程并发重试不会共用同一个 .tmp；最终 os.replace 到
    # 同一个确定文件名 batch-<run_id>.json，因此始终只有一份正式副本。
    from uuid import uuid4
    tmp = f"{path}.tmp-{os.getpid()}-{uuid4().hex[:8]}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(_file_envelope(entry), handle, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)  # 原子改名；重试只覆盖同一个 batch-<id>.json
    return path


def read_cold_file(run_id):
    path = _file_path(run_id)
    if path is None or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def canonical_safe(value):
    try:
        return canonical_json(value)
    except (TypeError, ValueError):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()


# --------------------------------------------------------------------------- #
# 阶段一：把副本写进冷档（按 run_id upsert，可安全重试）
# --------------------------------------------------------------------------- #

def _upsert_copy(db, bundle, actor):
    run, review = bundle["run"], bundle["review"]
    run_id = run["id"]
    receipt = run.get("result")
    signature = normalize_signature(review)
    snapshot = normalize_snapshot(run, review)
    ts = _now()

    existing = db.execute(
        select(cold_archives).where(cold_archives.c.run_id == run_id)
    ).mappings().first()

    values = {
        "run_id": run_id,
        "batch_no": str(run_id),
        "case_id": run["case_id"],
        "case_code": run.get("case_code"),
        "case_title": run.get("case_title"),
        "state": ArchiveState.COPIED,
        "status": ArchiveStatus.MIGRATING,
        "storage": "cold",
        "location": "cold",
        "receipt": receipt,
        "result": receipt,
        "signature": signature,
        "review": signature,
        "digest": run.get("digest"),
        "receipt_digest": run.get("digest"),
        "signature_digest": signature_digest(signature),
        "run_snapshot": snapshot,
        "source": run.get("storage", "hot"),
        "updated_at": ts,
    }

    if existing is None:
        values.update({
            "id": run_id,
            "copies": 1,
            "attempts": int(existing["attempts"]) + 1 if existing else 1,
            "actor": actor,
            "created_at": ts,
        })
        try:
            with db.begin_nested():
                db.execute(cold_archives.insert().values(**values))
        except IntegrityError:
            # 并发迁同一批次：唯一键兜底，转为更新既有行；
            # 但绝不把已完成切换的 active 终态降级回 copied。
            db.execute(cold_archives.update().where(
                cold_archives.c.run_id == run_id,
                cold_archives.c.state != ArchiveState.ACTIVE,
            ).values(**{k: v for k, v in values.items() if k != "id"}))
    else:
        db.execute(cold_archives.update().where(
            cold_archives.c.run_id == run_id,
            cold_archives.c.state != ArchiveState.ACTIVE,
        ).values(**values, attempts=int(existing["attempts"]) + 1))
    entry = find_by_identifier(db, run_id)
    try:
        path = write_cold_file(entry)
        if path:
            db.execute(cold_archives.update().where(
                cold_archives.c.run_id == run_id
            ).values(file_path=path))
            entry["file_path"] = path
    except OSError:
        logger.exception("write cold file failed run=%s", run_id)
        raise HTTPException(500, "冷档文件写入失败")
    return entry


# --------------------------------------------------------------------------- #
# 阶段二：用一次独立读回核对冷档三件
# --------------------------------------------------------------------------- #

def verify_archive(db, run_id):
    """独立从冷档表（及可选文件）读回并与旧库比对，在当前事务内落核对结果。

    返回 checks 字典；checks["all"] 为真才允许切换/清理。
    """
    cold = db.execute(
        select(cold_archives).where(cold_archives.c.run_id == run_id)
    ).mappings().first()
    if cold is None:
        raise HTTPException(404, "冷档中没有该批次")
    cold = dict(cold)
    bundle = load_hot_bundle(db, run_id)

    if bundle is None:
        # 旧库已清：以冷档内快照为基准做副本自洽校验。
        snapshot = cold.get("run_snapshot") or {}
        hot_receipt = snapshot.get("result", cold.get("receipt"))
        hot_signature = snapshot.get("review", cold.get("signature"))
        hot_digest = snapshot.get("digest", cold.get("digest"))
        source_absent = True
    else:
        hot_receipt = bundle["run"].get("result")
        hot_signature = bundle["review"]
        hot_digest = bundle["run"].get("digest")
        source_absent = False

    checks = compare_parts(
        hot_receipt=hot_receipt,
        hot_signature=hot_signature,
        hot_digest=hot_digest,
        cold_receipt=cold.get("receipt"),
        cold_signature=cold.get("signature"),
        cold_digest=cold.get("digest"),
    )
    if source_absent:
        checks["receipt"] = canonical_safe(cold.get("receipt")) == canonical_safe(hot_receipt)
        checks["signature"] = normalize_signature(cold.get("signature")) == normalize_signature(hot_signature)
        checks["digest"] = bool(cold.get("digest")) and checks["digest_recomputes"]
        checks["all"] = checks["receipt"] and checks["signature"] and checks["digest"]

    if cold.get("file_path") or COLD_ARCHIVE_DIR:
        try:
            file_data = read_cold_file(run_id)
        except (OSError, json.JSONDecodeError):
            file_data = None
        checks["file"] = bool(
            file_data
            and file_data.get("digest") == cold.get("digest")
            and canonical_safe(file_data.get("receipt")) == canonical_safe(cold.get("receipt"))
            and normalize_signature(file_data.get("signature")) == normalize_signature(cold.get("signature"))
        )
        checks["all"] = checks["all"] and checks["file"]

    # 条件更新：迟到的核对事务不能把已是终态 active 的冷档降级。
    # （并发迁档时，cutover 可能已在本函数读冷档之后提交 active。）
    target_state = ArchiveState.VERIFIED if checks["all"] else ArchiveState.MISMATCH
    db.execute(
        cold_archives.update()
        .where(cold_archives.c.run_id == run_id)
        .where(cold_archives.c.state != ArchiveState.ACTIVE)
        .values(checks=checks, verified_at=_now(), state=target_state)
    )
    return checks


def verify_committed(run_id):
    """供 API 调用：在独立事务中核对并持久化结果。"""
    with engine.begin() as db:
        return verify_archive(db, run_id)


# --------------------------------------------------------------------------- #
# 阶段三：原子切换 + 清旧库
# --------------------------------------------------------------------------- #

def _cutover(db, run_id, actor):
    """在调用方事务内执行。成功返回 archive dict；核对失败返回 {"_blocked": checks}。"""
    locked = db.execute(
        select(runs.c.id).where(runs.c.id == run_id).with_for_update()
    ).first()
    if locked is None:
        cold = find_by_identifier(db, run_id)
        if cold and cold["state"] == ArchiveState.ACTIVE:
            return cold
        raise HTTPException(409, "旧库批次不存在，且冷档尚未完成切换")

    checks = verify_archive(db, run_id)
    if not checks["all"]:
        # verify_archive 已把状态置为 mismatch；随调用方事务一起提交保留。
        return {"_blocked": checks}

    ts = _now()
    # 单事务：冷档置 active 与删除旧库签字/批次一起提交 = 回执一次性切换。
    db.execute(cold_archives.update().where(cold_archives.c.run_id == run_id).values(
        state=ArchiveState.ACTIVE,
        status=ArchiveStatus.ARCHIVED,
        archived_at=ts,
        sealed_at=ts,
        updated_at=ts,
        checks=checks,
    ))
    db.execute(reviews.delete().where(reviews.c.run_id == run_id))
    db.execute(runs.delete().where(runs.c.id == run_id))
    audit.record(db, "archive_run", actor, "run", run_id)
    return find_by_identifier(db, run_id)


# --------------------------------------------------------------------------- #
# 编排：一次调用完成 复制 -> 独立核对 -> 切换清理；任意阶段可安全重试
# --------------------------------------------------------------------------- #

def archive_run(run_id, actor="system", fault=None):
    # 0) 已完成切换：幂等成功，绝不新增副本。
    with engine.connect() as db:
        existing = find_by_identifier(db, run_id)
        hot = load_hot_bundle(db, run_id)

    if existing and existing["state"] == ArchiveState.ACTIVE and hot is None:
        return {"changed": False, "idempotent": True, "archive": existing}

    # 1) 资格校验 + 复制（独立事务提交，中断也留下唯一冷档行）。
    with engine.begin() as db:
        bundle = load_hot_bundle(db, run_id)
        if bundle is None:
            if existing:
                return {"changed": False, "idempotent": True, "archive": existing}
            raise HTTPException(404, "执行批次不存在")
        run, review = bundle["run"], bundle["review"]
        if run["status"] != JobState.DONE:
            raise HTTPException(409, "只有执行成功的批次才能迁入冷档")
        if review is None:
            raise HTTPException(409, "批次完成复核签署后才能迁入冷档")
        _upsert_copy(db, bundle, actor)

    if fault in ("after_copy", "copied", "interrupt", "crash"):
        raise MigrationInterrupted(run_id, "after_copy")

    # 2)+3) 独立读回核对与切换清理在同一事务：
    # 行锁串行化同一批次的并发迁档；核对不过则提交 mismatch 状态并保留旧库。
    blocked_checks = None
    with engine.begin() as db:
        outcome = _cutover(db, run_id, actor)
        if isinstance(outcome, dict) and outcome.get("_blocked"):
            blocked_checks = outcome["_blocked"]
        else:
            archive = outcome
    if blocked_checks is not None:
        raise HTTPException(409, {
            "message": "冷档独立读回核对未全部一致，已保留旧库批次，不能清理",
            "checks": blocked_checks,
        })
    return {"changed": True, "idempotent": False, "archive": archive}


def migrate_one(identifier, actor="system", fault=None):
    """接受批次号字符串的便捷封装。"""
    run_id = _coerce_run_id(identifier)
    return archive_run(run_id, actor=actor, fault=fault)


def purge_run(identifier, actor="operator"):
    """严格的“核对后清旧库”闸门：不重新复制，只认冷档现有副本的独立读回结果。

    对不上就 409 并原样保留旧库批次；对得上才在单事务里切换 + 清理。
    """
    run_id = _coerce_run_id(identifier)
    with engine.connect() as db:
        existing = find_by_identifier(db, run_id)
    if existing is None:
        raise HTTPException(404, "冷档中没有该批次副本，不能核对清理")
    if existing["state"] == ArchiveState.ACTIVE:
        return {"changed": False, "idempotent": True, "archive": existing, "verified": True}
    with engine.begin() as db:
        result = _cutover(db, run_id, actor)
        if isinstance(result, dict) and result.get("_blocked"):
            checks = result["_blocked"]
            raise HTTPException(409, {
                "message": "冷档独立读回核对未全部一致，已保留旧库批次，不能清理",
                "checks": checks,
            })
    return {"changed": True, "idempotent": False, "archive": result, "verified": True}


def _coerce_run_id(identifier):
    if isinstance(identifier, int):
        return identifier
    text = str(identifier).strip().lstrip("#")
    if text.upper().startswith("BATCH-"):
        text = text.split("-", 1)[1]
    if not text.isdigit():
        raise HTTPException(422, "批次号必须是原批次数字编号")
    return int(text)


def migrate_ready(actor="system", limit=20):
    """逐步迁：每次处理一批“已签 + done + 尚未在冷档 active”的旧批次。"""
    limit = max(1, min(int(limit or 20), 200))
    with engine.connect() as db:
        candidates = [row[0] for row in db.execute(
            select(runs.c.id)
            .join(reviews, reviews.c.run_id == runs.c.id)
            .where(runs.c.status == JobState.DONE)
            .order_by(runs.c.id)
            .limit(limit)
        )]
        cold_done = set(db.execute(
            select(cold_archives.c.run_id).where(
                cold_archives.c.state == ArchiveState.ACTIVE,
                cold_archives.c.run_id.in_(candidates or [0]),
            )
        ).scalars()) if candidates else set()

    migrated, skipped, failed = [], [], []
    for run_id in candidates:
        if run_id in cold_done:
            skipped.append(run_id)
            continue
        try:
            result = archive_run(run_id, actor=actor)
            (migrated if result["changed"] else skipped).append(run_id)
        except HTTPException as exc:
            failed.append({"run_id": run_id, "status": exc.status_code, "detail": exc.detail})
    return {
        "limit": limit,
        "candidates": candidates,
        "migrated": migrated,
        "skipped": skipped,
        "failed": failed,
        "counts": {"migrated": len(migrated), "skipped": len(skipped), "failed": len(failed)},
    }


# --------------------------------------------------------------------------- #
# 回执统一读取：旧库优先，缺失时整体落到冷档（绝不跨源拼字段）
# --------------------------------------------------------------------------- #

def get_run_anywhere(db, run_id):
    run_id = _coerce_run_id(run_id)
    bundle = load_hot_bundle(db, run_id)
    if bundle is not None:
        run = dict(bundle["run"])
        # 外连签字产生的标签列不属于 RunEntry / 回执字段，统一剔除。
        for key in ("review_run_id", "review_actor", "review_note", "review_created_at"):
            run.pop(key, None)
        run["archived"] = False
        run["storage"] = "hot"
        run["location"] = "hot"
        return run
    cold = db.execute(
        select(cold_archives).where(cold_archives.c.run_id == run_id)
    ).mappings().first()
    if cold is not None and cold["state"] == ArchiveState.ACTIVE:
        snapshot = cold["run_snapshot"] or {}
        run = {key: snapshot.get(key) for key in (
            "id", "case_id", "case_title", "case_code", "status", "parameter",
            "actor", "created_at", "finished_at", "result", "failure", "digest",
        )}
        run.update({
            "id": run_id,
            "archived": True,
            "storage": "cold",
            "location": "cold",
            "archived_at": cold["archived_at"],
        })
        return run
    raise HTTPException(404, "执行批次不存在")


def get_review_anywhere(db, run_id):
    run_id = _coerce_run_id(run_id)
    review = db.execute(
        select(reviews).where(reviews.c.run_id == run_id)
    ).mappings().first()
    if review is not None:
        return dict(review)
    cold = db.execute(
        select(cold_archives).where(cold_archives.c.run_id == run_id)
    ).mappings().first()
    if cold is not None and cold["state"] == ArchiveState.ACTIVE and cold["signature"]:
        return normalize_signature(cold["signature"])
    return None


def get_export_bundle(db, run_id):
    """与 GET /runs/{id}/export 完全相同的 JSON 结构，冷档时取归档快照。"""
    run_id = _coerce_run_id(run_id)
    bundle = load_hot_bundle(db, run_id)
    if bundle is not None:
        run = dict(bundle["run"])
        # 与原 GET /runs/{id}/export 的键集合保持一致：
        # 去掉外连带来的 case_code 与 review_* 标签列，只留 case_title + review。
        run.pop("case_code", None)
        for key in ("review_run_id", "review_actor", "review_note", "review_created_at"):
            run.pop(key, None)
        run["review"] = dict(bundle["review"]) if bundle["review"] else None
        return run
    cold = db.execute(
        select(cold_archives).where(cold_archives.c.run_id == run_id)
    ).mappings().first()
    if cold is not None and cold["state"] == ArchiveState.ACTIVE:
        return dict(cold["run_snapshot"] or {})
    raise HTTPException(404, "没有可导出的执行结果")


# --------------------------------------------------------------------------- #
# 演练资料引用计数与清理
# --------------------------------------------------------------------------- #

def case_reference_state(db, case_id):
    case = db.execute(select(cases).where(cases.c.id == case_id)).mappings().first()
    if case is None:
        raise HTTPException(404, "资料不存在")
    hot_batches = db.scalar(
        select(func.count()).select_from(runs).where(runs.c.case_id == case_id)
    )
    cold_batches = db.scalar(
        select(func.count()).select_from(cold_archives).where(
            cold_archives.c.case_id == case_id,
            cold_archives.c.state == ArchiveState.ACTIVE,
        )
    )
    total_batches = db.scalar(
        select(func.count()).select_from(cold_archives).where(cold_archives.c.case_id == case_id)
    ) + int(hot_batches or 0)
    return {
        "case_id": case_id,
        "hot_batches": int(hot_batches or 0),
        "cold_batches": int(cold_batches or 0),
        "archived_batches": int(cold_batches or 0),
        "total_batches": int(total_batches),
        # 旧库批次行是引用的唯一热证据；最后一个引用批次迁档后此行数才会归零。
        "cleanable": int(hot_batches or 0) == 0,
        "can_cleanup": int(hot_batches or 0) == 0,
        "reason": None if int(hot_batches or 0) == 0 else "仍有引用该资料的批次在旧库中，需等其归入冷档",
    }


def cleanup_case(case_id, actor="operator", soft=False):
    with engine.begin() as db:
        state = case_reference_state(db, case_id)
        if not state["cleanable"]:
            raise HTTPException(409, state["reason"] or "资料仍被旧库批次引用，暂不能清理")
        case = db.execute(select(cases).where(cases.c.id == case_id)).mappings().first()
        snapshot = dict(case) if case else None
        if soft:
            db.execute(cases.update().where(cases.c.id == case_id).values(
                title=f"[已清理] {case['title']}"[:120],
                description="",
                payload={"cleaned": True, "cleaned_at": _now()},
            ))
            action = "redact_case"
        else:
            db.execute(cases.delete().where(cases.c.id == case_id))
            action = "cleanup_case"
        audit.record(db, action, actor, "case", case_id)
    return {
        "deleted": not soft,
        "cleaned": True,
        "soft": soft,
        "case_id": case_id,
        "case": snapshot,
        "references": state,
    }
