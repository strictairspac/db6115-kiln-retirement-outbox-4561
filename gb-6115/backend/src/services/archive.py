"""兼容性别名：冷档 / 迁档领域服务。

权威实现见 ``src.services.archives``；这里用常见命名再导出一次，
方便按 ``archive`` / ``cold_archive`` 等不同习惯引用同一套操作。
"""
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

# 常见函数名别名。
migrate = archive_run
move_to_cold = archive_run
move_to_cold_storage = archive_run
archive_batch = archive_run
verify = verify_committed
get_receipt = get_run_anywhere
find_by_batch_no = find_by_identifier
cleanup_materials = cleanup_case
delete_case_when_drained = cleanup_case
migrate_batch = migrate_ready
purge = purge_run
cleanup = cleanup_case
