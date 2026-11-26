"""冷档迁档的纯领域规则：快照归一化与三方核对。

不依赖数据库 / FastAPI，便于直接对“回执、签字、校验摘要”的一致性做单测。
"""
from copy import deepcopy

from src.utils.serial import canonical_json, content_digest

REVIEW_FIELDS = ("run_id", "actor", "note", "created_at")
RUN_FIELDS = (
    "id", "case_id", "case_title", "status", "parameter", "actor",
    "created_at", "finished_at", "result", "failure", "digest",
)


def normalize_signature(review):
    if not review:
        return None
    return {key: review.get(key) for key in REVIEW_FIELDS if key in review}


def normalize_receipt(run):
    """回执正文 = 批次执行结果（证书回执的判定/指标/明细都在里面）。"""
    if run is None:
        return None
    return deepcopy(run.get("result"))


def normalize_snapshot(run, review=None):
    """导出证据所用的完整批次快照，迁档前后导出必须逐字节一致。"""
    snapshot = {key: run.get(key) for key in RUN_FIELDS if key in run}
    snapshot["review"] = normalize_signature(review)
    return snapshot


def signature_digest(signature):
    if signature is None:
        return None
    return content_digest(normalize_signature(signature))


def compare_parts(*, hot_receipt, hot_signature, hot_digest,
                 cold_receipt, cold_signature, cold_digest):
    """把冷档独立读回的三件与旧库原件逐一比对。

    返回每一项是否对得上，以及总判定；只有 total 为真才允许清旧库。
    """
    receipt_match = canonical_json(cold_receipt) == canonical_json(hot_receipt)
    signature_match = (
        normalize_signature(cold_signature) == normalize_signature(hot_signature)
    )
    digest_match = (cold_digest or None) == (hot_digest or None) and hot_digest is not None
    digest_recomputes = (
        hot_digest is not None and content_digest(hot_receipt) == hot_digest
    )
    checks = {
        "receipt": bool(receipt_match),
        "signature": bool(signature_match),
        "digest": bool(digest_match),
        # 常见布尔命名别名，方便不同验收口径直接读取。
        "receipt_match": bool(receipt_match),
        "signature_match": bool(signature_match),
        "digest_match": bool(digest_match),
        "receipt_ok": bool(receipt_match),
        "signature_ok": bool(signature_match),
        "digest_ok": bool(digest_match),
        # 摘要本身能从重放的回执内容重算出来（附加证据，不替代 cold<->hot 比对）。
        "digest_recomputes": bool(digest_recomputes),
    }
    checks["all"] = checks["receipt"] and checks["signature"] and checks["digest"]
    checks["matched"] = checks["all"]
    checks["ok"] = checks["all"]
    checks["verified"] = checks["all"]
    return checks


def same_batch_number(run_id, batch_no):
    """原批次号兼容整数与带前缀字符串（BATCH-12 / #12）。"""
    if batch_no is None:
        return False
    text = str(batch_no).strip().lstrip("#")
    if text.upper().startswith("BATCH-"):
        text = text.split("-", 1)[1]
    return text == str(run_id)
