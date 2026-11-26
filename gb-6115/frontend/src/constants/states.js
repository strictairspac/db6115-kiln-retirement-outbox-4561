export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  archive_run: "迁入冷档", cleanup_case: "清理资料", redact_case: "脱敏清理资料",
});
