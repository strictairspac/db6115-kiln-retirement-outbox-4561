"""兼容性别名：冷存储 / 冷档服务的常见模块名。"""
from src.services.archives import *  # noqa: F401,F403
from src.services.archives import (  # noqa: F401
    MigrationInterrupted,
    archive_run,
    case_reference_state,
    cleanup_case,
    find_by_identifier,
    get_archive,
    get_export_bundle,
    get_review_anywhere,
    get_run_anywhere,
    list_archives,
    migrate_one,
    migrate_ready,
    purge_run,
    verify_archive,
    verify_committed,
)

migrate_to_cold = archive_run
move_batch_to_cold = archive_run
archive_signed_runs = migrate_ready
delete_material = cleanup_case
