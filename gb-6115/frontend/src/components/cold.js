import { api } from "../api/workbench.js";
import { errorText } from "./common/fields.js";
import { el } from "../utils/dom.js";

// 同一 hash 重复查询时也强制主路由刷新。
export function showBatch(batchNo) {
  const id = String(batchNo ?? "").replace(/^#/, "").replace(/^BATCH-/i, "").trim();
  if (!id) return;
  const hash = `evidence/${encodeURIComponent(id)}`;
  if (location.hash === `#${hash}`) {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    location.hash = hash;
  }
}

export function batchLookup() {
  const input = el("input", {
    name: "batch_no", inputmode: "numeric", required: true,
    placeholder: "输入原批次号，如 12 或 BATCH-12",
    "aria-label": "按原批次号查询回执",
  });
  const issue = errorText();
  const form = el("form", {
    class: "batch-lookup", "data-testid": "batch-lookup",
    onsubmit: event => {
      event.preventDefault();
      const value = new FormData(form).get("batch_no");
      if (!/^\d+$/.test(String(value).replace(/^#/, "").replace(/^BATCH-/i, "").trim())) {
        issue.textContent = "请输入数字批次号";
        return;
      }
      issue.textContent = "";
      showBatch(value);
    },
  }, input, el("button", {type: "submit", "data-testid": "lookup-batch"}, "查询回执"), issue);
  return form;
}

export function storageBadge(run) {
  const cold = run?.archived || run?.storage === "cold";
  return el("span", {
    class: `storage-badge ${cold ? "is-cold" : "is-hot"}`,
    "data-testid": "storage-badge",
    "data-storage": cold ? "cold" : "hot",
  }, cold ? "冷档" : "旧库");
}

const CHECK_LABELS = [
  ["receipt", "回执内容"],
  ["signature", "签字"],
  ["digest", "校验摘要"],
  ["digest_recomputes", "摘要重算"],
  ["file", "冷档文件"],
];

export function checksPanel(checks) {
  if (!checks) return null;
  return el("ul", {class: "archive-checks", "data-testid": "archive-checks"},
    CHECK_LABELS.filter(([key]) => key in checks).map(([key, label]) =>
      el("li", {"data-check": key, "data-ok": checks[key] ? "true" : "false"},
        el("span", {}, label),
        el("strong", {}, checks[key] ? "一致" : "不一致"))));
}

export function archivePanel(run, user, {onChanged} = {}) {
  const issue = errorText();
  const region = el("div", {class: "archive-actions", "data-testid": "archive-panel"});
  const canWrite = user?.role === "operator" || user?.role === "reviewer";

  if (run.archived || run.storage === "cold") {
    api.archive(run.id).then(entry => {
      region.append(
        el("p", {class: "archive-state", "data-testid": "archive-state"},
          `该回执已在冷档（${entry.state || "active"}），原批次号 #${run.id} 不变。`),
        checksPanel(entry.checks));
    }).catch(() => {});
    region.append(el("p", {}, "回执已一次性切换到冷档，可继续按原批次号查询与导出。"));
    return el("div", {}, region, issue);
  }

  if (!canWrite) {
    region.append(el("p", {class: "empty"}, "只读账号可查询冷档回执；迁档由操作员/复核员执行。"));
    return el("div", {}, region);
  }

  const button = el("button", {
    type: "button", class: "archive-button", "data-testid": "archive-run", "data-run": run.id,
    onclick: async () => {
      button.disabled = true;
      issue.textContent = "";
      try {
        const result = await api.archiveRun(run.id);
        region.replaceChildren(
          el("p", {"data-testid": "archive-state"}, "已迁入冷档并完成独立核对，旧库批次已清理。"),
          checksPanel(result.checks));
        onChanged?.();
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      } catch (error) {
        issue.textContent = error.message;
        button.disabled = false;
      }
    },
  }, "迁入冷档");
  region.append(el("p", {class: "empty"}, "独立读回回执、签字与校验摘要，全部一致后才清理旧库批次；中断重试不会产生副本。"));
  region.append(button);
  return el("div", {}, region, issue);
}
