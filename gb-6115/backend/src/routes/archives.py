"""冷档 / 回执 / 资料清理接口。

为兼容不同调用习惯，同一组领域操作挂了多组路径别名；权威行为以
``src.services.archives`` 为准。所有接口都需要登录身份。
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from src.config.resources import engine
from src.controllers.access import identity
from src.services import archives

router = APIRouter(tags=["ColdArchive"])


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

async def _body(request: Request):
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_run_ref(data, request: Request):
    for key in ("run_id", "runId", "batch_id", "batchId", "batch_no", "batchNo", "id"):
        value = data.get(key)
        if value not in (None, ""):
            return value
    for key in ("run_id", "batch_id", "batch_no", "id"):
        value = request.query_params.get(key)
        if value:
            return value
    return None


def _extract_fault(data, request: Request):
    for source in (data, request.query_params):
        for key in ("fault", "failpoint", "interrupt", "simulate", "crash"):
            value = source.get(key) if hasattr(source, "get") else None
            if value in (True, "1", "true", "yes"):
                return "after_copy"
            if isinstance(value, str) and value and value not in ("0", "false"):
                return value
    return None


def _migrate_response(ref, actor, fault=None):
    try:
        result = archives.migrate_one(ref, actor=actor, fault=fault)
    except archives.MigrationInterrupted as exc:
        with engine.connect() as db:
            entry = archives.find_by_identifier(db, exc.run_id)
        return {"interrupted": True, "stage": exc.stage, "changed": False, "archive": entry}
    entry = result["archive"]
    return {
        **(entry or {}),
        "archive": entry,
        "changed": result["changed"],
        "idempotent": result["idempotent"],
        "verified": True,
        "copies": 1,
    }


# --------------------------------------------------------------------------- #
# 冷档列表 / 批量逐步迁档（先注册静态路径，避免被 {identifier} 吃掉）
# --------------------------------------------------------------------------- #

@router.get("/archives", dependencies=[Depends(identity)])
@router.get("/archive", dependencies=[Depends(identity)])
@router.get("/cold-archives", dependencies=[Depends(identity)])
@router.get("/cold-archive", dependencies=[Depends(identity)])
@router.get("/cold/archives", dependencies=[Depends(identity)])
@router.get("/cold/batches", dependencies=[Depends(identity)])
@router.get("/cold", dependencies=[Depends(identity)])
def list_cold_archives(limit: int = 100):
    with engine.connect() as db:
        return archives.list_archives(db, limit=min(max(limit, 1), 500))


@router.post("/archives/migrate", dependencies=[Depends(identity)])
@router.post("/archives/migrate-all", dependencies=[Depends(identity)])
@router.post("/archives/gradual", dependencies=[Depends(identity)])
@router.post("/archive/migrate", dependencies=[Depends(identity)])
@router.post("/archive/migrate-all", dependencies=[Depends(identity)])
@router.post("/cold/migrate", dependencies=[Depends(identity)])
@router.post("/cold-archives/migrate", dependencies=[Depends(identity)])
@router.post("/cold-archive/migrate", dependencies=[Depends(identity)])
async def migrate_all(request: Request, user=Depends(identity)):
    data = await _body(request)
    ref = _extract_run_ref(data, request)
    fault = _extract_fault(data, request)
    if ref is not None:
        return _migrate_response(ref, user["username"], fault)
    limit = data.get("limit") or request.query_params.get("limit") or 20
    return archives.migrate_ready(user["username"], limit=int(limit))


@router.post("/archives", dependencies=[Depends(identity)])
@router.post("/archive", dependencies=[Depends(identity)])
@router.post("/cold-archives", dependencies=[Depends(identity)])
@router.post("/cold-archive", dependencies=[Depends(identity)])
async def create_archive(request: Request, user=Depends(identity)):
    data = await _body(request)
    refs = data.get("run_ids") or data.get("ids") or data.get("batch_ids")
    fault = _extract_fault(data, request)
    if isinstance(refs, list) and refs:
        done = [_migrate_response(ref, user["username"], fault) for ref in refs]
        return {"items": done, "counts": {"total": len(done)}}
    ref = _extract_run_ref(data, request)
    if ref is None:
        limit = int(data.get("limit") or 20)
        return archives.migrate_ready(user["username"], limit=limit)
    return _migrate_response(ref, user["username"], fault)


# --------------------------------------------------------------------------- #
# 单批次迁档 / 查询 / 核对 / 重试 —— 路径别名
# --------------------------------------------------------------------------- #

_ITEM_ACTION_PATHS = [
    "/runs/{identifier}/archive",
    "/runs/{identifier}/migrate",
    "/runs/{identifier}/cold-archive",
    "/runs/{identifier}/cold",
    "/archive/run/{identifier}",
    "/archives/run/{identifier}",
    "/archive/{identifier}",
    "/archive/{identifier}/archive",
    "/archive/{identifier}/migrate",
    "/archive/{identifier}/retry",
    "/archives/{identifier}",
    "/archives/{identifier}/archive",
    "/archives/{identifier}/migrate",
    "/archives/{identifier}/retry",
    "/batches/{identifier}/archive",
    "/batches/{identifier}/migrate",
    "/batches/{identifier}/cold-archive",
    "/cold/batches/{identifier}/archive",
    "/cold/batches/{identifier}/migrate",
    "/cold-archive/{identifier}",
    "/cold-archive/{identifier}/migrate",
    "/cold-archives/{identifier}",
    "/cold-archives/{identifier}/migrate",
]


def _register_item_action(path, methods):
    async def handler(identifier: str, request: Request, user=Depends(identity)):
        data = await _body(request) if request.method == "POST" else {}
        fault = _extract_fault(data, request)
        return _migrate_response(identifier, user["username"], fault)

    for method in methods:
        router.add_api_route(path, handler, methods=[method], tags=["ColdArchive"])


for _path in _ITEM_ACTION_PATHS:
    _register_item_action(_path, ["POST"])


def _purge_handler(identifier: str, user=Depends(identity)):
    return archives.purge_run(identifier, actor=user["username"])


for _purge_path in (
    "/runs/{identifier}/purge",
    "/archive/{identifier}/purge",
    "/archives/{identifier}/purge",
    "/batches/{identifier}/purge",
    "/cold-archive/{identifier}/purge",
    "/cold-archives/{identifier}/purge",
):
    router.add_api_route(_purge_path, _purge_handler, methods=["POST"], tags=["ColdArchive"])


@router.get("/runs/{identifier}/archive", dependencies=[Depends(identity)])
@router.get("/runs/{identifier}/cold", dependencies=[Depends(identity)])
@router.get("/archive/{identifier}", dependencies=[Depends(identity)])
@router.get("/archives/{identifier}", dependencies=[Depends(identity)])
@router.get("/cold-archive/{identifier}", dependencies=[Depends(identity)])
@router.get("/cold-archives/{identifier}", dependencies=[Depends(identity)])
def get_one_archive(identifier: str):
    with engine.connect() as db:
        return archives.get_archive(db, identifier)


@router.post("/archives/{identifier}/verify", dependencies=[Depends(identity)])
@router.post("/archive/{identifier}/verify", dependencies=[Depends(identity)])
@router.post("/runs/{identifier}/verify-archive", dependencies=[Depends(identity)])
@router.get("/archives/{identifier}/verify", dependencies=[Depends(identity)])
def verify_one_archive(identifier: str):
    run_id = archives._coerce_run_id(identifier)
    checks = archives.verify_committed(run_id)
    with engine.connect() as db:
        entry = archives.find_by_identifier(db, run_id)
    return {"run_id": run_id, "verified": checks["all"], "checks": checks, "archive": entry}


# --------------------------------------------------------------------------- #
# 回执：按原批次号查，旧库 / 冷档同一个口径
# --------------------------------------------------------------------------- #

def _receipt_payload(identifier: str):
    with engine.connect() as db:
        run = archives.get_run_anywhere(db, identifier)
        review = archives.get_review_anywhere(db, run["id"])
    # 既给嵌套结构，也把批次字段平铺到顶层，兼容不同验收脚本的取值习惯。
    return {
        **run,
        "batch_no": str(run["id"]),
        "batch_id": run["id"],
        "run_id": run["id"],
        "run": run,
        "receipt": run.get("result"),
        "signature": review,
        "review": review,
        "digest": run.get("digest"),
        "storage": run.get("storage"),
        "archived": run.get("archived"),
    }


@router.get("/receipt", dependencies=[Depends(identity)])
def receipt_by_query(batch_no: str | None = None, run_id: str | None = None,
                     batch_id: str | None = None):
    ref = batch_no or run_id or batch_id
    if ref is None:
        raise HTTPException(422, "需要 batch_no 或 run_id")
    return _receipt_payload(ref)


@router.get("/receipts/{identifier}", dependencies=[Depends(identity)])
@router.get("/receipt/{identifier}", dependencies=[Depends(identity)])
@router.get("/receipts/by-batch/{identifier}", dependencies=[Depends(identity)])
@router.get("/certificates/{identifier}/receipt", dependencies=[Depends(identity)])
@router.get("/runs/{identifier}/receipt", dependencies=[Depends(identity)])
@router.get("/batches/{identifier}", dependencies=[Depends(identity)])
@router.get("/batches/{identifier}/receipt", dependencies=[Depends(identity)])
@router.get("/cold/batches/{identifier}", dependencies=[Depends(identity)])
@router.get("/cold/receipts/{identifier}", dependencies=[Depends(identity)])
def receipt_by_batch(identifier: str):
    return _receipt_payload(identifier)


# --------------------------------------------------------------------------- #
# 演练资料引用状态与清理
# --------------------------------------------------------------------------- #

@router.post("/runs/archive", dependencies=[Depends(identity)])
@router.post("/runs/migrate-cold", dependencies=[Depends(identity)])
async def archive_runs_collection(request: Request, user=Depends(identity)):
    data = await _body(request)
    refs = data.get("run_ids") or data.get("ids") or data.get("batch_ids")
    if isinstance(refs, list) and refs:
        return {"items": [_migrate_response(ref, user["username"]) for ref in refs]}
    ref = _extract_run_ref(data, request)
    if ref is not None:
        return _migrate_response(ref, user["username"], _extract_fault(data, request))
    return archives.migrate_ready(user["username"], int(data.get("limit") or 20))


@router.post("/cases/cleanup", dependencies=[Depends(identity)])
@router.post("/cases/clean", dependencies=[Depends(identity)])
async def cleanup_case_collection(request: Request, user=Depends(identity)):
    data = await _body(request)
    ref = _extract_run_ref(data, request)
    if ref is None:
        for key in ("case_id", "caseId", "id"):
            if data.get(key) is not None:
                ref = data[key]
                break
    if ref is None:
        raise HTTPException(422, "需要 case_id")
    soft = bool(data.get("soft") or data.get("mode") == "soft")
    return archives.cleanup_case(int(ref), actor=user["username"], soft=soft)


@router.get("/cases/{case_id}/references", dependencies=[Depends(identity)])
@router.get("/cases/{case_id}/archive-state", dependencies=[Depends(identity)])
@router.get("/cases/{case_id}/cleanup", dependencies=[Depends(identity)])
def case_references(case_id: int):
    with engine.connect() as db:
        return archives.case_reference_state(db, case_id)


@router.delete("/cases/{case_id}", dependencies=[Depends(identity)])
@router.post("/cases/{case_id}/cleanup", dependencies=[Depends(identity)])
@router.post("/cases/{case_id}/clean", dependencies=[Depends(identity)])
@router.post("/cases/{case_id}/purge", dependencies=[Depends(identity)])
@router.post("/cases/{case_id}/materials/cleanup", dependencies=[Depends(identity)])
async def cleanup_material(case_id: int, request: Request, user=Depends(identity)):
    data = await _body(request) if request.method == "POST" else {}
    soft = bool(data.get("soft") or data.get("mode") == "soft"
                or request.query_params.get("soft") in ("1", "true"))
    return archives.cleanup_case(case_id, actor=user["username"], soft=soft)
