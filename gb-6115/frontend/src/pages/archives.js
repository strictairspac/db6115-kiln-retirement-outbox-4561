import { api } from "../api/workbench.js";
import { batchLookup, checksPanel, storageBadge } from "../components/cold.js";
import { errorText } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { table } from "../components/common/table.js";
import { el } from "../utils/dom.js";
import { time } from "../utils/format.js";

export async function archivesPage(config, user) {
  const archives = await api.archives();
  const issue = errorText();
  const canWrite = user.role === "operator" || user.role === "reviewer";

  const migrateButton = canWrite ? el("button", {
    type: "button", "data-testid": "migrate-ready",
    onclick: async () => {
      migrateButton.disabled = true;
      issue.textContent = "";
      try {
        const result = await api.migrateReady(20);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
        return result;
      } catch (error) {
        issue.textContent = error.message;
        migrateButton.disabled = false;
      }
    },
  }, "批量迁入已签旧批次") : null;

  return el("div", {class: "page archives-page", "data-route": "archives"},
    pageHeading("冷档库", "已签旧批次核对后归入冷档腾库，回执仍按原批次号查询。", migrateButton),
    issue,
    section("按原批次号查回执", batchLookup()),
    section("冷档批次", archives.length ? table(
      ["原批次号", "资料", "状态", "核对", "入库时间", "查询"],
      archives.map(item => [
        el("strong", {"data-testid": "archive-batch", "data-run": item.run_id}, `#${item.run_id}`),
        item.case_title || item.case_id,
        el("span", {class: `archive-state state-${item.state}`}, item.status === "archived" ? "已入冷档" : item.state),
        checksSummary(item.checks),
        time(item.archived_at || item.created_at),
        el("a", {href: `#evidence/${item.run_id}`}, "证书回执"),
      ]), "冷档批次") : empty("还没有批次迁入冷档；已签署并执行成功的批次可在证书回执页迁入。")),
    section("存储说明", el("div", {class: "stack"},
      storageBadge({storage: "cold", archived: true}),
      el("p", {class: "empty"}, "每个原批次号在冷档只有一份副本；中断后重试在同一记录上续写。回执、签字与校验摘要独立读回全部一致，并在单次事务中完成切换与旧库清理。"))));
}

function checksSummary(checks) {
  if (!checks) return "—";
  const ok = checks.all === true;
  const node = el("span", {
    class: ok ? "checks-ok" : "checks-bad",
    "data-testid": "archive-checks-summary",
    "data-ok": ok ? "true" : "false",
  }, ok ? "三项一致" : "存在不一致");
  node.append(checksPanel(checks));
  return node;
}
