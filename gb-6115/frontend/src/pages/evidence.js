import { api } from "../api/workbench.js";
import { archivePanel, batchLookup, storageBadge } from "../components/cold.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { resultTable } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note), el("time", {}, time(review.created_at)));
}

export async function evidencePage(config, user, route) {
  // 选择器包含已在冷档的批次；按原批次号直达时不依赖该列表。
  const runs = (await api.runs(true)).filter(item => item.status === "done");
  let run;
  let loadError = null;
  if (route.id) {
    try {
      run = await api.run(route.id);
    } catch (error) {
      loadError = error;
      run = runs.find(item => item.id === route.id);
    }
  } else {
    run = runs[0];
  }

  const lookup = section("按原批次号查询", batchLookup(),
    el("p", {class: "empty"}, el("a", {href: "#archives", "data-testid": "open-archives"}, "打开冷档库 →")));

  if (loadError && !run) {
    return el("div", {class: "page"},
      pageHeading(config.modules[3], null), lookup,
      empty(`没有查到批次 #${route.id} 的回执：${loadError.message}`));
  }

  if (!run?.result) return el("div", {class: "page"},
    pageHeading(config.modules[3], null), lookup,
    empty("成功执行一个批次后可以查看、签核、迁冷档和下载证据。"),
    el("a", {href: "#runs"}, config.modules[2]));

  const review = await api.review(run.id).catch(() => null);
  const issue = errorText();
  const button = el("button", {type: "button", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "导出证据");
  const signing = el("div", {});
  signing.append(review ? signature(review) : user.role === "reviewer" ?
    reviewForm(run.id, data => signing.replaceChildren(signature(data))) : empty("待复核员签署。"));
  const options = el("select", {"aria-label": "选择已完成的批次", onchange: event => {
    location.hash = `evidence/${event.target.value}`;
  }}, runs.map(item => el("option", {
    value: item.id, selected: item.id === run.id,
  }, `#${item.id} ${item.archived ? "〔冷档〕" : ""}${item.case_title || ""}`)));

  const headingAction = el("div", {class: "heading-actions"}, storageBadge(run), button);
  return el("div", {class: "page evidence"},
    pageHeading(config.modules[3], run.case_title, headingAction),
    issue,
    lookup,
    options,
    el("section", {class: "feature evidence-summary"}, el("div", {}, el("h2", {}, "批次判定"), el("p", {}, run.result.summary)),
      el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest))),
    el("dl", {class: "metrics"}, Object.entries(run.result.metrics || {}).map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))),
    section("业务结果明细", run.result.rows?.length ? resultTable(run.result.rows) : empty("此批次无明细。")),
    section("复核签署", signing),
    section("冷档迁档", archivePanel(run, user)));
}
