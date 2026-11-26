import os

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.environ["JWT_SECRET"]
DEMO_PASSWORD = os.environ["DEMO_PASSWORD"]
SEED_DEMO = os.environ.get("SEED_DEMO", "true").lower() == "true"
# 可选的冷档文件副本目录（compose 中指向共享产物卷）。不配置时冷档以数据库冷表为准。
COLD_ARCHIVE_DIR = os.environ.get("COLD_ARCHIVE_DIR") or None
