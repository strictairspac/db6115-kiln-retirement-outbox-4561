# 冷档迁档（腾库）验收记录

对应需求口径：

> 已签的旧批次逐步迁到冷档腾库，证书回执页面仍按原批次号查。冷档独立读出的回执、签字和校验摘要都对得上，才可清掉旧库里的批次。迁档中断后重试不新增副本，回执一次性切换到冷档，期间照常查询。演练资料要等最后一个引用批次归入冷档才清理，迁档时也能用仍在旧库的资料发起新批次。

## 1. 需求 → 实现对照

| 验收口径 | 实现位置 | 保证方式 |
|---|---|---|
| 只迁“已签 + 成功”的旧批次 | `services/archives.py::archive_run` | 批次必须 `status=done` 且存在 `reviews` 签字，否则 `409`，不写冷档 |
| 逐步迁 | `POST /archives/migrate`、`migrate_ready(limit)` | 每次处理一批按编号排序的合格批次，返回 `migrated/skipped/failed` |
| 冷档独立读出“回执、签字、校验摘要”核对 | `domain/archive.py::compare_parts`、`verify_archive` | 用一次**新的 SELECT** 从冷档表读回 `receipt/signature/digest`，与旧库原件逐项 canonical-JSON 比对；另校验摘要可由回执重算 |
| 对得上才清旧库 | `_cutover` | `checks.all` 为假时返回 `409`，旧库 `runs/reviews` 不删，冷档置 `mismatch` |
| 中断重试不新增副本 | `cold_archives.run_id UNIQUE` + 按 `run_id` upsert | 任意阶段中断后重试都在同一行续写；文件副本用确定文件名 `batch-<run_id>.json` + 原子 `os.replace`，重试只覆盖同一文件 |
| 回执一次性切换到冷档 | `_cutover` 单事务 | 冷档置 `active`、删除旧库 `reviews`+`runs` 在同一事务提交；读路径旧库优先、缺失才整体读冷档，不跨源拼字段 |
| 期间照常按原批次号查询 | `GET /runs/{id}`、`/runs/{id}/review`、`/runs/{id}/export`、`GET /receipts/{批次号}` | 旧库在就读旧库；旧库清了就用原 `run_id` 读 `active` 冷档，导出 JSON 键结构与迁档前一致 |
| 资料等最后一个引用批次归冷才清理 | `case_reference_state` / `cleanup_case` | 只要 `runs` 里还有该 `case_id` 的行就 `409`；最后一个批次迁档删行后 `cleanable=true`，才允许 `DELETE /cases/{id}` |
| 迁档时仍可用在旧库的资料发起新批次 | `POST /cases/{id}/runs` 原逻辑不变 | 迁档不锁/不删资料；新批次产生新的 `runs` 行，会重新把资料置为不可清理 |

并发安全：同一批次的切换用 `SELECT … FOR UPDATE` 行锁串行化；冷档状态推进带 `state <> 'active'`
条件，迟到的核对/复制不会把终态 `active` 降级。已用 8 线程并发迁同一批次验证：始终恰好 1 行冷档、
终态 `active`、旧库行清除、回执可读。

## 2. 数据与状态

新表 `cold_archives`（启动时 `metadata.create_all` 幂等建表，旧数据卷可直接升级）：

- `id = run_id`：主键直接取原批次号；`run_id`、`batch_no` 均唯一。
- `case_id` 不建外键：资料清理后冷档回执仍独立留存、按原批次号可查。
- `receipt/result`（回执正文）、`signature/review`（签字）、`digest`（校验摘要）、
  `run_snapshot`（完整导出快照）、`checks`（最近核对结果）。
- 状态：`copying → copied → verified → active`；核对失败为 `mismatch`。
- 可选文件副本：环境变量 `COLD_ARCHIVE_DIR`（compose 指向共享卷
  `/var/lib/workbench/cold`），关闭时以冷档表为准。

## 3. 接口（均需登录；同一能力提供多组路径别名）

主路径：

- `POST /runs/{run_id}/archive`：复制 → 独立核对 → 原子切换/清理（幂等，可安全重试）。
- `POST /archives/migrate`，body 可选 `{"limit": 20}` 或 `{"run_id": 12}`：逐步批量迁。
- `POST /archives/{run_id}/verify`：只做独立读回核对，返回
  `checks.{receipt,signature,digest,all}`。
- `POST /archives/{run_id}/purge`：严格闸门，不重新复制，只按现有冷档副本核对后清理。
- `GET /archives`：冷档列表；`GET /archives/{run_id}`：冷档详情。
- `GET /receipts/{批次号}`、`GET /receipt?batch_no=BATCH-12`、`GET /runs/{id}/receipt`、
  `GET /batches/{批次号}`：按原批次号取回执/签字/摘要。
- `GET /cases/{id}/references`：`{hot_batches,cold_batches,cleanable,reason}`。
- `DELETE /cases/{id}` 或 `POST /cases/{id}/cleanup`：引用清空后清理资料。

兼容别名前缀：`/archive`、`/cold`、`/cold-archive(s)`、`/batches`，以及
`/runs/archive`、`/cases/cleanup` 等；字段同时给出 `id/run_id/batch_id/batch_no`、
`archived/storage/location`、`verified/matched/ok` 等常见命名。

## 4. 前端

- 「证书回执」页（`frontend/src/pages/evidence.js`）新增：
  - `[aria-label="按原批次号查询回执"]` 查询框（支持 `12`、`#12`、`BATCH-12`）；
  - `[data-testid="storage-badge"]` 旧库/冷档标记；
  - `[data-testid="archive-run"]`「迁入冷档」按钮与
    `[data-testid="archive-checks"]` 三项核对结果；
  - 批次下拉带「〔冷档〕」项；既有签署 `[data-testid="reviewed"]`、`#review-form`、导出不变。
- 「资料」页资料目录新增「冷档清理」列与 `[data-testid="cleanup-case"]`，
  悬停读取引用状态，被引用时点击会显示后端返回的拦截原因。
- 新增「冷档库」导航与 `frontend/src/pages/archives.js`：
  `[data-testid="migrate-ready"]`、`[data-testid="archive-batch"]`，每行可跳
  `#evidence/{原批次号}`。

## 5. 已执行的验证（作者环境，无 Docker daemon）

后端用 Python 3.11 + FastAPI TestClient + SQLite（开启 `sqlite_autoincrement`
使删行后批次号不复用，贴近 PostgreSQL 序列语义）跑了 61 项断言，全部通过，覆盖：

- 未登录 `401`；未签/失败批次 `409` 且不产冷档；
- 正常迁档后冷档恰好 1 行、旧库 run/review 删除、`checks.all=true`；
- `GET /runs/{id}`、`/review`、`/receipts/{id}`、`?batch_no=BATCH-id/#id` 全部按原批次号读冷档；
- 迁档前后 `/runs/{id}/export` 字节一致；
- 连续三种路径重试仍只有 1 行冷档；
- `fault=after_copy` 制造“复制后中断”：留下唯一 `copied` 行、旧库仍在，重试后 `active`；
- 篡改冷档摘要：`verify` 报 `digest=false`，`purge` 返回 `409` 且旧库保留、状态 `mismatch`；
  幂等 `archive` 以旧库为准重抄后修复并完成切换；
- 资料引用计数：有热批次时 `cleanup/DELETE` 均 `409`；迁一个后仍被另一个阻止；
  最后一个迁完可删，删除后冷档回执仍 `200` 且保留 `case_id`；
- 迁档中断期间可再建/插入新批次，新批次重新阻止资料清理；
- `migrate` 批量与 `GET /archives` 正确。

结果：

```
RESULT: 61 passed, 0 failed
```

另：

- 8 线程并发迁同一批次 ×3 轮：每轮均为 1 行冷档 / `active` / 旧库已清（并修复了
  状态降级与临时文件竞争）。
- 真实 `uvicorn` HTTP + curl 走通登录 → `POST /runs/1/archive` →
  `GET /receipt?batch_no=BATCH-1`（`storage=cold`，签字/摘要齐全），
  并生成共享冷档文件 `batch-1.json`（含 `file:true` 核对）。
- jsdom 渲染冒烟 17 项全过（回执查询框、旧库/冷档标记、迁入按钮、核对面板、
  签署形态、资料清理按钮、冷档库列表与跳转）；另验证未签时复核员仍见 `#review-form`。
- 全部前端 JS 通过 `node --check`；后端 `compileall` 通过；`docker-compose.yml` YAML 解析通过。

## 6. 在 Docker/PostgreSQL 上复演（评审环境）

```sh
cd gb-6115
cp -n .env.example .env
docker compose up -d --build --wait

# 走原业务：登录 operator / Demo-DbWorkbench-2026，选用 ovens-retirement-1124.json
# 资料提交批次（默认参数 12），等 worker 完成；再用 reviewer 登录在「证书回执」签署。

TOK=$(curl -s -X POST http://localhost:19316/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"Demo-DbWorkbench-2026"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s -X POST http://localhost:19316/runs/1/archive -H "Authorization: Bearer $TOK"
curl -s "http://localhost:19316/receipts/1" -H "Authorization: Bearer $TOK"
curl -s "http://localhost:19316/cases/1/references" -H "Authorization: Bearer $TOK"
```

界面打开 <http://localhost:18316>，在「证书回执」用原批次号查询、迁入冷档，
在「资料」页清理已无引用的演练资料，在「冷档库」查看全部冷档。

> 说明：作者沙箱内没有 docker / PostgreSQL / 浏览器，未生成真实页面截图；
> 上文第 5 节为实际执行的自动化与真实 HTTP 验证结果。README 所述
> `../../test-artifacts/gb-6115/TESTING.md`（即 `/test-artifacts/gb-6115/TESTING.md`）
> 在本沙箱不可创建（根目录只读），故验收记录先存于仓库内 `gb-6115/TESTING.md`。
