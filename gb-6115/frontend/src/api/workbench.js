import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: (includeArchived = false) => request(`/runs${includeArchived ? "?all=true" : ""}`),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),

  // 冷档迁档与回执按原批次号查询。
  archives: () => request("/archives"),
  archive: id => request(`/archives/${id}`),
  archiveRun: (id, data = {}) => request(`/runs/${id}/archive`, {method: "POST", data}),
  verifyArchive: id => request(`/archives/${id}/verify`, {method: "POST"}),
  purgeArchive: id => request(`/archives/${id}/purge`, {method: "POST"}),
  migrateReady: (limit = 20) => request("/archives/migrate", {method: "POST", data: {limit}}),
  receipt: id => request(`/receipts/${id}`),

  // 演练资料引用状态与清理。
  caseReferences: id => request(`/cases/${id}/references`),
  cleanupCase: (id, soft = false) =>
    request(`/cases/${id}/cleanup${soft ? "?soft=true" : ""}`, {method: "POST", data: {}}),
};
