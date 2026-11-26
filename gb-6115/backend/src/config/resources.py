from redis import Redis
from sqlalchemy import create_engine

from .settings import DATABASE_URL, REDIS_URL

_engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    # 便于轻量验收 / CI 用 SQLite 跑通同一套服务；PostgreSQL 行为不受影响。
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_kwargs)
cache = Redis.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=3)
