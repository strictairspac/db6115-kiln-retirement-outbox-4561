"""兼容性别名：冷档服务的另一常见模块名。"""
from src.services.archive import *  # noqa: F401,F403
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
    verify_archive,
    verify_committed,
)

cold_migrate = archive_run
