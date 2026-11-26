from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.config.resources import engine
from src.constants.job_state import JobState
from src.controllers.access import identity, reviewer
from src.controllers.workbench import new_review
from src.schemas.reviews import ReviewEntry, ReviewInput
from src.schemas.runs import RunEntry
from src.services import archives, reviews, runs
from src.utils.serial import canonical_json

router = APIRouter(prefix="/runs", tags=["Execution"])


@router.get("", response_model=list[RunEntry], dependencies=[Depends(identity)])
def list_runs(include: str | None = Query(default=None), all: bool = False):
    with engine.connect() as db:
        entries = runs.list_entries(db)
        # 默认只列旧库在库批次；include=archived / all=true 时把冷档批次一并带回。
        if all or include in ("archived", "all", "cold"):
            hot_ids = {item["id"] for item in entries}
            for item in archives.list_archives(db, limit=200):
                if item["run_id"] in hot_ids:
                    continue
                snapshot = item.get("run_snapshot") or {}
                entries.append({
                    "id": item["run_id"],
                    "case_id": item["case_id"],
                    "case_title": item.get("case_title") or snapshot.get("case_title"),
                    "status": snapshot.get("status", JobState.DONE),
                    "parameter": snapshot.get("parameter", 0),
                    "actor": snapshot.get("actor", item.get("actor", "system")),
                    "created_at": snapshot.get("created_at", item["created_at"]),
                    "finished_at": snapshot.get("finished_at"),
                    "result": item.get("receipt"),
                    "failure": snapshot.get("failure"),
                    "digest": item.get("digest"),
                    "archived": True,
                    "storage": "cold",
                    "archived_at": item.get("archived_at"),
                })
            entries.sort(key=lambda row: row["id"], reverse=True)
        return entries


@router.get("/{run_id}", response_model=RunEntry, dependencies=[Depends(identity)])
def run_detail(run_id: int):
    # 旧库查不到时整体回落到冷档，证书回执仍按原批次号读取。
    with engine.connect() as db:
        return archives.get_run_anywhere(db, run_id)


@router.get("/{run_id}/review", response_model=ReviewEntry | None, dependencies=[Depends(identity)])
def review_detail(run_id: int):
    with engine.connect() as db:
        archives.get_run_anywhere(db, run_id)
        return archives.get_review_anywhere(db, run_id)


@router.post("/{run_id}/review", response_model=ReviewEntry, status_code=201)
def sign(run_id: int, form: ReviewInput, user=Depends(reviewer)):
    return new_review(run_id, form, user)


@router.get("/{run_id}/export", dependencies=[Depends(identity)])
def export(run_id: int):
    with engine.connect() as db:
        run = archives.get_export_bundle(db, run_id)
        if run.get("status") != JobState.DONE:
            raise HTTPException(409, "没有可导出的执行结果")
    return Response(canonical_json(run), media_type="application/json", headers={
        "Content-Disposition": f"attachment; filename=evidence-{run_id}.json",
    })
