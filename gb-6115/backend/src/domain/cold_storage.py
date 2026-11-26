"""兼容性别名：冷存储纯领域规则，权威实现见 ``src.domain.archive``。"""
from src.domain.archive import *  # noqa: F401,F403
from src.domain.archive import (  # noqa: F401
    compare_parts,
    normalize_receipt,
    normalize_signature,
    normalize_snapshot,
    same_batch_number,
    signature_digest,
)
