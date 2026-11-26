import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { table } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { navigate } from "../router/router.js";
import { el } from "../utils/dom.js";

function queueForm(config, selected) {
  const form = el("form", {class: "queue-form"},
    field(config.parameter_label, el("input", {
      name: "parameter", type: "number", min: 1, max: config.max_parameter, required: true, value: config.default_parameter,
    })),
    el("button", {type: "submit"}, "提交执行"), errorText());
  submit(form, async values => {
    const run = await api.enqueue(selected.id, {parameter: Number(values.get("parameter"))});
    navigate(`runs/${run.id}`);
  });
  return form;
}

function registration(config, selected) {
  const data = selected ? JSON.stringify(selected.payload, null, 2) : "{}";
  const form = el("form", {id: "case-form"},
    field("名称", el("input", {name: "title", required: true, minlength: 2, maxlength: 120, value: selected ? `${selected.title} · 复演` : ""})),
    field("说明", el("input", {name: "description", maxlength: 420, value: selected?.description || ""})),
    field(`${config.unit}内容`, el("textarea", {name: "payload", spellcheck: "false", required: true, rows: 12}, data),
      selected ? "可以保留演示数据，也可以编辑 JSON 后保存。" : "输入业务 JSON。"),
    errorText(), el("button", {type: "submit"}, "保存资料"));
  submit(form, async values => {
    let payload;
    try {
      payload = JSON.parse(values.get("payload"));
    } catch {
      throw new Error("资料 JSON 无法解析，请检查括号、引号和逗号");
    }
    const entry = await api.createCase({title: values.get("title"), description: values.get("description"), payload});
    navigate(`cases/${entry.id}`);
  });
  return form;
}

function cleanupCell(item, user) {
  const feedback = el("span", {class: "cleanup-feedback", role: "alert"});
  if (user.role === "viewer") return el("span", {class: "empty"}, "只读");
  const button = el("button", {
    type: "button", class: "quiet danger", "data-testid": "cleanup-case", "data-case": item.id,
    onclick: async () => {
      button.disabled = true;
      feedback.textContent = "";
      try {
        await api.cleanupCase(item.id);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      } catch (error) {
        feedback.textContent = error.message;
        button.disabled = false;
      }
    },
  }, "清理资料");
  // 点击前先拉引用状态：不可清理时给出原因，可清理时按钮仍需再点一次执行。
  const state = el("small", {class: "ref-state", "data-testid": "case-ref-state"});
  button.addEventListener("mouseenter", () => {
    if (state.dataset.loaded) return;
    api.caseReferences(item.id).then(ref => {
      state.dataset.loaded = "1";
      state.textContent = ref.cleanable ? "最后引用批次已在冷档，可清理" : ref.reason;
      state.dataset.cleanable = ref.cleanable ? "true" : "false";
    }).catch(() => {});
  });
  return el("div", {class: "cleanup-cell"}, button, state, feedback);
}

export async function casesPage(config, user, route) {
  const cases = await api.cases();
  const selected = cases.find(item => item.id === route.id) ?? cases.at(-1);
  const operator = user.role === "operator";
  return el("div", {class: "page"},
    pageHeading(config.modules[1], `${config.unit} · ${config.fixture}`),
    section("资料目录", cases.length ? table(["编号", "名称", "说明", "操作", "冷档清理"], cases.map(item =>
      [item.code, item.title, item.description,
        el("a", {href: `#cases/${item.id}`, "aria-current": item.id === selected?.id ? "true" : null}, "选用"),
        cleanupCell(item, user)]), "资料目录") :
      empty("这里还没有资料。")),
    el("div", {class: "split case-split"},
      section(selected ? selected.title : "等待选择资料", selected ?
        [el("p", {}, selected.description), operator ? queueForm(config, selected) : empty("当前账号可查看此资料。"),
          el("details", {}, el("summary", {}, "查看业务数据"), el("pre", {}, JSON.stringify(selected.payload, null, 2)))] :
        empty("先登记一条资料，再提交执行。")),
      operator ? section("登记新资料", registration(config, selected)) : null));
}
