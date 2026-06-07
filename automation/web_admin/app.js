const state = {
  section: "dashboard",
  user: "",
  selectedTaskIds: new Set(),
  selectedJobIds: new Set(),
  lastSettings: null,
};

const titles = {
  dashboard: ["仪表盘", "系统健康、任务和异常集中视图"],
  workers: ["电脑管理", "在线状态、同步分组和客户端信息"],
  groups: ["分组账号", "别名、账号状态和分组同步管理"],
  jobs: ["任务中心", "排队、执行、失败、取消和抢占任务"],
  plans: ["账号评分", "评分计划、调度和中央提示词"],
  schedule: ["调度任务", "计划任务批量暂停、恢复和取消"],
  audit: ["审计设置", "后台操作记录、模型配置和系统配置摘要"],
};

const statusText = {
  queued: "排队中",
  leased: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  cancel_requested: "取消中",
  preempted: "已抢占",
  active: "启用",
  inactive: "停用",
  online: "在线",
  offline: "离线",
  scheduled: "待执行",
  paused: "已暂停",
  dispatched: "已派发",
  expired_missed: "已过期",
  auto_scheduled: "已生成调度",
  draft: "草稿",
  deleted: "已删除",
  cancelled_by_user: "用户取消",
  cancelled_by_new_plan: "新计划取消",
  cancelled_account_inactive: "账号停用取消",
  account_inactive: "账号停用",
  score_plan_collected: "评分已生成",
  stub: "兜底计划",
  ok: "成功",
};

const metricLabels = [
  ["likes", "点赞"],
  ["bookmarks", "收藏"],
  ["retweets", "转帖"],
  ["replies", "评论"],
  ["follows", "关注"],
  ["posts", "发帖"],
  ["manual_searches", "搜索"],
];

const fallbackFields = [
  ["slot_count", "每天时间点数"],
  ["start_time", "开始时间"],
  ["end_time", "结束时间"],
  ["min_gap_minutes", "最小间隔分钟"],
  ["likes_min", "点赞最小"],
  ["likes_max", "点赞最大"],
  ["bookmarks_min", "收藏最小"],
  ["bookmarks_max", "收藏最大"],
  ["retweets_min", "转帖最小"],
  ["retweets_max", "转帖最大"],
  ["replies_min", "评论最小"],
  ["replies_max", "评论最大"],
  ["follows_min", "关注最小"],
  ["follows_max", "关注最大"],
  ["posts_min", "发帖最小"],
  ["posts_max", "发帖最大"],
  ["manual_searches_min", "搜索最小"],
  ["manual_searches_max", "搜索最大"],
  ["daily_follows_max", "每日关注上限"],
  ["daily_posts_max", "每日发帖上限"],
];

function $(id) {
  return document.getElementById(id);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function fmtTime(ts) {
  const n = Number(ts || 0);
  if (!n) return "-";
  return new Date(n * 1000).toLocaleString("zh-CN", { hour12: false });
}

function statusLabel(status) {
  return statusText[String(status || "")] || String(status || "-");
}

function statusPill(status) {
  const s = String(status || "-");
  const cls = ["completed", "active", "online", "scheduled", "auto_scheduled", "ok"].includes(s)
    ? "ok"
    : ["failed", "cancelled", "cancel_requested", "deleted", "inactive", "cancelled_by_user", "cancelled_by_new_plan", "cancelled_account_inactive", "account_inactive"].includes(s)
      ? "bad"
      : ["expired_missed", "paused", "preempted"].includes(s)
        ? "warn"
        : "blue";
  return `<span class="pill ${cls}">${esc(statusLabel(s))}</span>`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    cache: "no-store",
    ...options,
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function table(headers, rows, empty = "暂无数据") {
  if (!rows.length) return `<div class="muted">${empty}</div>`;
  return `<div class="table-wrap"><table><thead><tr>${headers.join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function th(label) {
  return `<th>${esc(label)}</th>`;
}

function selectAllTh(kind) {
  return `<th><input type="checkbox" aria-label="全选" onchange="toggleAll('${kind}', this.checked)"></th>`;
}

function showHtmlDetail(title, html) {
  $("dialogTitle").textContent = title;
  $("dialogBody").innerHTML = html;
  $("detailDialog").showModal();
}

function showDetail(title, data) {
  showHtmlDetail(title, `<pre>${esc(typeof data === "string" ? data : JSON.stringify(data, null, 2))}</pre>`);
}

async function refreshCurrent() {
  const btn = $("refreshBtn");
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "刷新中...";
  $("refreshHint").textContent = "";
  try {
    await loadCurrent();
    $("refreshHint").textContent = `最后刷新：${new Date().toLocaleString("zh-CN", { hour12: false })}`;
  } catch (err) {
    $("refreshHint").textContent = `刷新失败：${err.message}`;
    throw err;
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

function setSection(section) {
  state.section = section;
  document.querySelectorAll("#nav button").forEach(btn => btn.classList.toggle("active", btn.dataset.section === section));
  document.querySelectorAll(".section").forEach(el => el.classList.toggle("active", el.id === section));
  $("pageTitle").textContent = titles[section][0];
  $("pageSubtitle").textContent = titles[section][1];
  refreshCurrent().catch(err => alert(err.message));
}

async function checkSession() {
  try {
    const data = await api("/admin/api/me");
    state.user = data.user;
    $("userBadge").textContent = data.user;
    $("loginView").classList.add("hidden");
    $("appView").classList.remove("hidden");
    await refreshCurrent();
  } catch (_) {
    $("appView").classList.add("hidden");
    $("loginView").classList.remove("hidden");
  }
}

async function login(event) {
  event.preventDefault();
  $("loginError").textContent = "";
  try {
    const data = await api("/admin/api/login", {
      method: "POST",
      body: JSON.stringify({ username: $("loginUser").value.trim(), password: $("loginPass").value }),
    });
    state.user = data.user;
    $("userBadge").textContent = data.user;
    $("loginView").classList.add("hidden");
    $("appView").classList.remove("hidden");
    await refreshCurrent();
  } catch (err) {
    $("loginError").textContent = err.message;
  }
}

async function logout() {
  await api("/admin/api/logout", { method: "POST", body: "{}" });
  location.reload();
}

async function loadDashboard() {
  const data = await api("/admin/api/dashboard");
  const s = data.stats;
  const cards = [
    ["在线电脑", s.workers_online],
    ["离线电脑", s.workers_offline],
    ["活跃账号", s.accounts_active],
    ["今日任务", s.jobs_today],
    ["今日失败", s.jobs_failed_today],
    ["待执行调度", s.schedule_pending_today],
  ];
  $("statGrid").innerHTML = cards.map(([label, value]) => `<div class="stat-card"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`).join("");
  $("runningJobs").innerHTML = renderJobsMini(data.running_jobs || []);
  const issues = [
    ...(data.recent_failures || []).map(j => ({ type: "失败任务", text: `job=${j.id} ${j.job_type} ${j.error || ""}`, time: j.updated_at })),
    ...(data.missed_tasks || []).map(t => ({ type: "错过调度", text: `task=${t.id} plan=${t.plan_id || "-"} ${t.last_error || ""}`, time: t.updated_at })),
    ...(data.workers || []).filter(w => !w.online).map(w => ({ type: "离线电脑", text: `${w.node_id} ${w.label || ""} 已离线 ${w.offline_seconds || 0} 秒`, time: w.last_seen })),
  ].slice(0, 20);
  $("todayIssues").innerHTML = table([th("类型"), th("说明"), th("时间")], issues.map(i => `<tr><td>${esc(i.type)}</td><td class="wrap">${esc(i.text)}</td><td>${fmtTime(i.time)}</td></tr>`), "今日暂无异常");
}

function renderJobsMini(jobs) {
  return table([th("ID"), th("电脑"), th("类型"), th("来源"), th("状态"), th("更新时间")], jobs.map(j => `
    <tr>
      <td>${j.id}</td>
      <td>${esc(j.leased_by || j.target_node_id || "-")}</td>
      <td>${esc(j.job_type)}${Number(j.priority || 0) >= 100 ? ' <span class="pill blue">最高优先级</span>' : ""}</td>
      <td>${esc(j.source_label || "-")}</td>
      <td>${statusPill(j.status)}</td>
      <td>${fmtTime(j.updated_at)}</td>
    </tr>`), "暂无执行中任务");
}

async function loadWorkers() {
  const data = await api("/admin/api/workers?limit=200");
  $("workersTable").innerHTML = table([
    th("电脑"), th("标签"), th("在线"), th("Grok"), th("中央同步分组"), th("运行上报分组"), th("账号来源分组"), th("一致性"), th("当前任务"), th("最后心跳"), th("操作"),
  ], data.workers.map(w => {
    const cfg = w.central_config || {};
    const current = (w.meta || {}).current_job || {};
    const central = w.central_sync_group_ids || cfg.sync_group_ids || [];
    const runtime = w.runtime_sync_group_ids || [];
    const accounts = w.account_group_ids || [];
    const mismatch = w.sync_mismatch || w.account_mismatch;
    return `<tr>
      <td>${esc(w.node_id)}</td>
      <td>${esc(w.label)}</td>
      <td>${w.online ? statusPill("online") : statusPill("offline")}</td>
      <td>${cfg.enable_grok_browser ? statusPill("active") : statusPill("inactive")}</td>
      <td class="wrap">${esc(central.join(", ") || "-")}</td>
      <td class="wrap">${esc(runtime.join(", ") || "-")}</td>
      <td class="wrap">${esc(accounts.join(", ") || "-")}</td>
      <td>${mismatch ? '<span class="pill warn">配置不一致</span>' : '<span class="pill ok">一致</span>'}</td>
      <td class="wrap">${current.id ? `job=${esc(current.id)} ${esc(current.job_type || "")}` : "-"}</td>
      <td>${fmtTime(w.last_seen)}<div class="small muted">${w.offline_seconds || 0} 秒前</div></td>
      <td>
        <button class="ghost" onclick="editWorkerConfig('${esc(w.node_id)}')">配置</button>
        <button class="ghost" onclick='showDetail("电脑详情", ${JSON.stringify(w).replaceAll("'", "&#39;")})'>详情</button>
      </td>
    </tr>`;
  }));
}

async function loadGroups() {
  const data = await api("/admin/api/groups?limit=300");
  $("groupsTable").innerHTML = table([th("别名"), th("group_id"), th("启用账号"), th("停用账号"), th("来源电脑"), th("同步电脑"), th("历史别名"), th("操作")], data.groups.map(g => `
    <tr>
      <td>${esc(g.alias || "-")}</td>
      <td>${esc(g.group_id)}</td>
      <td>${g.account_count || 0}</td>
      <td>${g.inactive_count || 0}</td>
      <td class="wrap">${esc(g.node_ids || "-")}</td>
      <td class="wrap">${esc(g.sync_node_ids || "-")}</td>
      <td>${g.alias_history_count || 0}</td>
      <td>
        ${g.alias ? `<button class="ghost danger" onclick="deleteAlias('${esc(g.alias)}')">解绑</button>` : ""}
        <button class="ghost" onclick="loadAccounts('${esc(g.group_id)}')">账号</button>
      </td>
    </tr>`));
}

async function loadAccounts(groupId = "") {
  if (groupId) $("accountFilter").value = groupId;
  const query = new URLSearchParams();
  const filter = $("accountFilter").value.trim();
  if (filter) query.set("group_id", filter);
  query.set("include_inactive", $("includeInactive").checked ? "1" : "0");
  query.set("limit", "1000");
  const data = await api(`/admin/api/accounts?${query.toString()}`);
  $("accountsTable").innerHTML = table([th("账号"), th("profile_id"), th("分组"), th("电脑"), th("账号状态"), th("电脑在线"), th("最后同步"), th("操作")], data.accounts.map(a => `
    <tr>
      <td>${esc(a.display_name || a.profile_name || a.x_username || "-")}</td>
      <td>${esc(a.profile_id)}</td>
      <td>${esc(a.group_id || "-")}</td>
      <td>${esc(a.node_id || "-")}</td>
      <td>${statusPill(a.status)}</td>
      <td>${a.node_online ? statusPill("online") : statusPill("offline")}</td>
      <td>${fmtTime(a.last_seen)}</td>
      <td>
        <button class="ghost" onclick="accountTimeline('${esc(a.profile_id)}')">时间线</button>
        ${a.status === "active"
          ? `<button class="ghost danger" onclick="setAccountStatus('${esc(a.profile_id)}','inactive')">停用</button>`
          : `<button class="ghost" onclick="setAccountStatus('${esc(a.profile_id)}','active')">恢复</button>`}
      </td>
    </tr>`));
}

async function loadJobs() {
  state.selectedJobIds.clear();
  const query = new URLSearchParams();
  if ($("jobStatus").value) query.set("status", $("jobStatus").value);
  if ($("jobSource").value) query.set("source", $("jobSource").value);
  if ($("jobNode").value.trim()) query.set("node_id", $("jobNode").value.trim());
  if ($("jobGroup").value.trim()) query.set("group_id", $("jobGroup").value.trim());
  if ($("jobAccount").value.trim()) query.set("account_id", $("jobAccount").value.trim());
  if ($("jobMode").value.trim()) query.set("mode", $("jobMode").value.trim());
  query.set("limit", "500");
  const data = await api(`/admin/api/jobs?${query.toString()}`);
  $("jobsTable").innerHTML = table([selectAllTh("jobs"), th("ID"), th("来源"), th("类型"), th("模式"), th("优先级"), th("电脑"), th("状态"), th("抢占关系"), th("错误"), th("更新"), th("操作")], data.jobs.map(j => `
    <tr>
      <td><input class="job-check" type="checkbox" onchange="toggleJob(${j.id}, this.checked)"></td>
      <td>${j.id}</td>
      <td>${esc(j.source_label || "-")}</td>
      <td>${esc(j.job_type)}</td>
      <td>${esc((j.payload || {}).mode || "-")}</td>
      <td>${j.priority || 10}${Number(j.priority || 0) >= 100 ? ' <span class="pill blue">最高</span>' : ""}</td>
      <td>${esc(j.leased_by || j.target_node_id || "-")}</td>
      <td>${statusPill(j.status)}</td>
      <td class="wrap">${relationText(j)}</td>
      <td class="wrap">${esc(j.error || "")}</td>
      <td>${fmtTime(j.updated_at)}</td>
      <td>
        <button class="ghost" onclick="jobDetail(${j.id})">日志</button>
        ${["queued", "leased"].includes(j.status) ? `<button class="ghost danger" onclick="cancelJob(${j.id})">取消</button>` : ""}
      </td>
    </tr>`));
}

function relationText(job) {
  const parts = [];
  if (job.preempted_by_job_id) parts.push(`被模式二 job=${job.preempted_by_job_id} 抢占`);
  if (job.requeued_job_id) parts.push(`已重排 job=${job.requeued_job_id}`);
  if (job.preempted_from_job_id) parts.push(`来自 job=${job.preempted_from_job_id}`);
  return esc(parts.join("；") || "-");
}

async function loadPlans() {
  const query = new URLSearchParams();
  if ($("planGroup").value.trim()) query.set("group_id", $("planGroup").value.trim());
  query.set("limit", "300");
  const [plans] = await Promise.all([api(`/admin/api/score-plans?${query.toString()}`), loadPrompt(), loadFallbackConfig()]);
  $("plansTable").innerHTML = table([th("ID"), th("账号"), th("分组"), th("评分"), th("状态"), th("来源 job"), th("调度汇总"), th("创建"), th("操作")], plans.plans.map(p => `
    <tr>
      <td>${p.id}</td>
      <td>${esc(p.account_display || p.account_name || p.account_id)}</td>
      <td>${esc(p.group_id || "-")}</td>
      <td>${esc(p.score ?? "-")}</td>
      <td>${statusPill(p.status)}</td>
      <td>${esc(p.source_job_id || "-")}</td>
      <td>${esc(summaryText(p.task_summary || {}))}</td>
      <td>${fmtTime(p.created_at)}</td>
      <td>
        <button class="ghost" onclick="planDetail(${p.id})">详情</button>
        <button class="ghost" onclick="planAction(${p.id}, 'pause')">暂停</button>
        <button class="ghost" onclick="planAction(${p.id}, 'resume')">恢复</button>
        <button class="ghost danger" onclick="planAction(${p.id}, 'delete')">删除</button>
      </td>
    </tr>`));
}

function summaryText(summary) {
  return Object.entries(summary).map(([k, v]) => `${statusLabel(k)}:${v}`).join("，") || "-";
}

async function loadPrompt() {
  const data = await api("/admin/api/score-prompt");
  $("scorePrompt").value = data.prompt || "";
}

async function loadFallbackConfig() {
  const data = await api("/admin/api/score-fallback-config");
  $("fallbackConfigBox").innerHTML = renderFallbackConfig(data.config || {});
}

function renderFallbackConfig(cfg) {
  const rows = fallbackFields.map(([key, label]) => `
    <tr>
      <th>${esc(label)}</th>
      <td><input data-fallback-key="${esc(key)}" value="${esc(cfg[key] ?? "")}"></td>
    </tr>`).join("");
  return `<div class="table-wrap compact"><table><tbody>${rows}</tbody></table></div>`;
}

async function loadSchedule() {
  state.selectedTaskIds.clear();
  const query = new URLSearchParams();
  if ($("taskStatus").value) query.set("status", $("taskStatus").value);
  if ($("taskNode").value.trim()) query.set("node_id", $("taskNode").value.trim());
  if ($("taskGroup").value.trim()) query.set("group_id", $("taskGroup").value.trim());
  if ($("taskAccount").value.trim()) query.set("account_id", $("taskAccount").value.trim());
  if ($("taskMode").value.trim()) query.set("mode", $("taskMode").value.trim());
  query.set("sort", "latest");
  query.set("limit", "800");
  const data = await api(`/admin/api/schedule?${query.toString()}`);
  $("scheduleTable").innerHTML = table([selectAllTh("tasks"), th("ID"), th("执行时间"), th("电脑"), th("分组"), th("账号"), th("模式"), th("状态"), th("plan/job"), th("错误"), th("操作")], data.tasks.map(t => `
    <tr>
      <td><input class="task-check" type="checkbox" onchange="toggleTask(${t.id}, this.checked)"></td>
      <td>${t.id}</td>
      <td>${fmtTime(t.run_at)}</td>
      <td>${esc(t.node_id || "-")}</td>
      <td>${esc(t.group_id || "-")}</td>
      <td>${esc(t.account_id)}</td>
      <td>${esc((t.payload || {}).mode || "-")}</td>
      <td>${statusPill(t.status)}</td>
      <td>plan=${esc(t.plan_id || "-")}<br>job=${esc(t.job_id || "-")}</td>
      <td class="wrap">${esc(t.last_error || "")}</td>
      <td>
        <button class="ghost" onclick='showDetail("调度任务 ${t.id}", ${JSON.stringify(t).replaceAll("'", "&#39;")})'>详情</button>
        <button class="ghost" onclick="taskAction(${t.id}, 'pause')">暂停</button>
        <button class="ghost" onclick="taskAction(${t.id}, 'resume')">恢复</button>
        <button class="ghost danger" onclick="taskAction(${t.id}, 'cancel')">取消</button>
      </td>
    </tr>`));
}

async function loadAudit() {
  const [audit, settings] = await Promise.all([
    api("/admin/api/audit?limit=200"),
    api("/admin/api/settings"),
  ]);
  state.lastSettings = settings.settings || {};
  $("auditTable").innerHTML = table([th("ID"), th("管理员"), th("动作"), th("目标"), th("结果"), th("时间")], audit.logs.map(l => `
    <tr>
      <td>${l.id}</td>
      <td>${esc(l.actor)}</td>
      <td>${esc(l.action)}</td>
      <td>${esc(l.target_type)}:${esc(l.target_id)}</td>
      <td>${l.ok ? statusPill("ok") : statusPill("failed")}</td>
      <td>${fmtTime(l.created_at)}</td>
    </tr>`));
  $("settingsBox").innerHTML = renderSettings(settings.settings || {});
}

function renderSettings(settings) {
  const cfg = settings.worker_default_config || {};
  const model = cfg.model_config || {};
  return `
    <div class="table-wrap compact"><table><tbody>
      <tr><th>管理员</th><td>${esc(settings.admin_user || "-")}</td></tr>
      <tr><th>Token 指纹</th><td>${esc(settings.token_fingerprint || "-")}</td></tr>
      <tr><th>数据库</th><td class="wrap">${esc(settings.db_path || "-")}</td></tr>
      <tr><th>模型启用</th><td><label class="check"><input id="modelEnabled" type="checkbox" ${model.enabled ? "checked" : ""}>启用</label></td></tr>
      <tr><th>Base URL</th><td><input id="modelBaseUrl" value="${esc(model.base_url || "")}"></td></tr>
      <tr><th>API Key</th><td><input id="modelApiKey" value="${esc(model.api_key || "")}" placeholder="留空或保留掩码则不替换"></td></tr>
      <tr><th>模型名称</th><td><input id="modelName" value="${esc(model.model || "")}"></td></tr>
    </tbody></table></div>`;
}

async function loadCurrent() {
  if (state.section === "dashboard") return loadDashboard();
  if (state.section === "workers") return loadWorkers();
  if (state.section === "groups") { await loadGroups(); return loadAccounts(); }
  if (state.section === "jobs") return loadJobs();
  if (state.section === "plans") return loadPlans();
  if (state.section === "schedule") return loadSchedule();
  if (state.section === "audit") return loadAudit();
}

async function bindAlias() {
  const alias = $("aliasName").value.trim();
  const group_id = $("aliasGroupId").value.trim();
  if (!alias || !group_id) return alert("请填写别名和 group_id");
  await api("/admin/api/groups/alias", { method: "POST", body: JSON.stringify({ alias, group_id }) });
  await refreshCurrent();
}

async function deleteAlias(alias) {
  if (!confirm(`确认解绑别名 ${alias}？`)) return;
  await api("/admin/api/groups/alias/delete", { method: "POST", body: JSON.stringify({ alias }) });
  await refreshCurrent();
}

async function addSyncGroup() {
  const node_id = $("syncNodeId").value.trim();
  const group_id = $("syncGroupId").value.trim();
  if (!node_id || !group_id) return alert("请填写 node_id 和 group_id");
  await api("/admin/api/worker-sync-groups", { method: "POST", body: JSON.stringify({ node_id, group_id }) });
  await refreshCurrent();
}

async function setAccountStatus(profileId, status) {
  if (status === "inactive" && !confirm(`停用账号 ${profileId} 会取消未执行计划，确认继续？`)) return;
  await api(`/admin/api/accounts/${encodeURIComponent(profileId)}/status`, { method: "POST", body: JSON.stringify({ status }) });
  await loadAccounts();
}

async function accountTimeline(profileId) {
  const data = await api(`/admin/api/accounts/${encodeURIComponent(profileId)}/timeline`);
  showDetail(`账号时间线 ${profileId}`, data.timeline);
}

async function jobDetail(jobId) {
  const data = await api(`/admin/api/jobs/${jobId}`);
  showDetail(`任务 ${jobId}`, data);
}

async function cancelJob(jobId) {
  if (!confirm(`确认取消任务 ${jobId}？`)) return;
  await api(`/admin/api/jobs/${jobId}/cancel`, { method: "POST", body: JSON.stringify({ reason: "cancelled_by_admin" }) });
  await loadJobs();
}

async function planDetail(planId) {
  const data = await api(`/admin/api/score-plans/${planId}`);
  const plan = data.plan || {};
  const tasks = data.tasks || [];
  const days = ((plan.parsed_plan || {}).days || []);
  const planRows = [];
  for (const day of days) {
    for (const slot of (day.slots || [])) {
      const metrics = slot.metrics || {};
      planRows.push(`<tr>
        <td>${esc(day.day_index || "-")}</td>
        <td>${esc(day.date || "-")}</td>
        <td>${esc(slot.time || "-")}</td>
        ${metricLabels.map(([key]) => `<td>${esc(metrics[key] ?? 0)}</td>`).join("")}
      </tr>`);
    }
  }
  const taskRows = tasks.map(t => `<tr>
    <td>${t.id}</td>
    <td>${fmtTime(t.run_at)}</td>
    <td>${esc((t.payload || {}).mode || "-")}</td>
    <td>${statusPill(t.status)}</td>
    <td>${esc(t.job_id || "-")}</td>
    <td class="wrap">${esc(t.last_error || "")}</td>
  </tr>`);
  showHtmlDetail(`评分计划 ${planId}`, `
    <div class="detail-section">
      <p><b>账号：</b>${esc(plan.account_display || plan.account_id || "-")}　<b>状态：</b>${statusPill(plan.status)}　<b>调度：</b>${esc(summaryText(plan.task_summary || {}))}</p>
      ${table([th("天"), th("日期"), th("时间"), ...metricLabels.map(([, label]) => th(label))], planRows, "没有解析到计划表格")}
    </div>
    <div class="detail-section">
      <h3>生成的调度任务</h3>
      ${table([th("任务ID"), th("执行时间"), th("模式"), th("状态"), th("job"), th("错误")], taskRows, "暂无调度任务")}
    </div>`);
}

async function planAction(planId, action) {
  const labels = { pause: "暂停", resume: "恢复", delete: "删除", "cancel-all": "取消全部" };
  if (["delete", "cancel-all"].includes(action) && !confirm(`确认对计划 ${planId} 执行${labels[action]}？`)) return;
  await api(`/admin/api/score-plans/${planId}/${action}`, { method: "POST", body: "{}" });
  await loadPlans();
}

function toggleTask(taskId, checked) {
  checked ? state.selectedTaskIds.add(taskId) : state.selectedTaskIds.delete(taskId);
}

function toggleJob(jobId, checked) {
  checked ? state.selectedJobIds.add(jobId) : state.selectedJobIds.delete(jobId);
}

function toggleAll(kind, checked) {
  const cls = kind === "jobs" ? ".job-check" : ".task-check";
  const set = kind === "jobs" ? state.selectedJobIds : state.selectedTaskIds;
  set.clear();
  document.querySelectorAll(cls).forEach(input => {
    input.checked = checked;
    if (checked) set.add(Number(input.closest("tr").children[1].textContent));
  });
}

async function taskAction(taskId, action) {
  if (action === "cancel" && !confirm(`确认取消调度任务 ${taskId}？`)) return;
  await api(`/admin/api/scheduled-tasks/${taskId}/${action}`, { method: "POST", body: "{}" });
  await loadSchedule();
}

async function bulkTask(action) {
  const task_ids = [...state.selectedTaskIds];
  if (!task_ids.length) return alert("请先勾选调度任务");
  if (action === "cancel" && !confirm(`确认取消 ${task_ids.length} 个调度任务？`)) return;
  await api("/admin/api/schedule/bulk", { method: "POST", body: JSON.stringify({ task_ids, action }) });
  await loadSchedule();
}

async function bulkCancelJobs() {
  const job_ids = [...state.selectedJobIds];
  if (!job_ids.length) return alert("请先勾选任务");
  if (!confirm(`确认取消 ${job_ids.length} 个任务？`)) return;
  await api("/admin/api/jobs/bulk", { method: "POST", body: JSON.stringify({ action: "cancel", job_ids }) });
  await loadJobs();
}

async function cleanupStaleJobs() {
  if (!confirm("确认清理过期自动任务和旧队列？")) return;
  const data = await api("/admin/api/maintenance/cleanup-stale", { method: "POST", body: JSON.stringify({ grace_seconds: 3600, include_score_jobs: true }) });
  alert(`已清理：调度过期 ${data.scheduled_expired || 0}，队列取消 ${data.jobs_cancelled || 0}`);
  await refreshCurrent();
}

async function savePrompt() {
  if (!confirm("确认保存中央评分提示词？这会影响后续账号评分计划。")) return;
  await api("/admin/api/score-prompt", { method: "POST", body: JSON.stringify({ prompt: $("scorePrompt").value }) });
  alert("已保存");
}

async function resetPrompt() {
  if (!confirm("确认恢复默认中央评分提示词？")) return;
  const data = await api("/admin/api/score-prompt/reset", { method: "POST", body: "{}" });
  $("scorePrompt").value = data.prompt || "";
}

async function saveFallbackConfig() {
  const payload = {};
  document.querySelectorAll("[data-fallback-key]").forEach(input => {
    payload[input.dataset.fallbackKey] = input.value.trim();
  });
  await api("/admin/api/score-fallback-config", { method: "POST", body: JSON.stringify(payload) });
  alert("兜底计划配置已保存");
  await loadFallbackConfig();
}

async function saveModelConfig() {
  const existing = ((state.lastSettings || {}).worker_default_config || {});
  const oldModel = existing.model_config || {};
  const keyText = $("modelApiKey").value.trim();
  const model_config = {
    enabled: $("modelEnabled").checked,
    provider: "openai_compatible",
    base_url: $("modelBaseUrl").value.trim(),
    model: $("modelName").value.trim(),
  };
  if (keyText && !keyText.includes("***") && !keyText.includes("...")) {
    model_config.api_key = keyText;
  }
  await api("/admin/api/worker-default-config", { method: "POST", body: JSON.stringify({ model_config }) });
  alert("默认模型配置已保存");
  await loadAudit();
}

async function editWorkerConfig(nodeId) {
  const data = await api(`/admin/api/workers/${encodeURIComponent(nodeId)}/config`);
  const cfg = data.config || {};
  const label = prompt("电脑标签", cfg.label || nodeId);
  if (label === null) return;
  const groupsText = prompt("中央同步分组，多个用逗号或换行分隔", (cfg.sync_group_ids || []).join(", "));
  if (groupsText === null) return;
  const grok = confirm("是否开启 Grok 浏览器？点击取消则关闭。");
  await api(`/admin/api/workers/${encodeURIComponent(nodeId)}/config`, {
    method: "POST",
    body: JSON.stringify({ label, enable_grok_browser: grok, open_gui_for_legacy: false }),
  });
  await api(`/admin/api/workers/${encodeURIComponent(nodeId)}/sync-groups`, {
    method: "POST",
    body: JSON.stringify({ sync_group_ids: groupsText }),
  });
  await loadWorkers();
}

document.addEventListener("DOMContentLoaded", () => {
  $("loginForm").addEventListener("submit", login);
  $("logoutBtn").addEventListener("click", logout);
  $("refreshBtn").addEventListener("click", () => refreshCurrent().catch(err => alert(err.message)));
  $("closeDialog").addEventListener("click", () => $("detailDialog").close());
  document.querySelectorAll("#nav button").forEach(btn => btn.addEventListener("click", () => setSection(btn.dataset.section)));
  $("bindAliasBtn").addEventListener("click", bindAlias);
  $("addSyncGroupBtn").addEventListener("click", addSyncGroup);
  $("loadAccountsBtn").addEventListener("click", () => loadAccounts());
  $("loadJobsBtn").addEventListener("click", loadJobs);
  $("bulkCancelJobsBtn").addEventListener("click", bulkCancelJobs);
  $("cleanupStaleBtn").addEventListener("click", cleanupStaleJobs);
  $("loadPlansBtn").addEventListener("click", loadPlans);
  $("loadScheduleBtn").addEventListener("click", loadSchedule);
  $("bulkPauseBtn").addEventListener("click", () => bulkTask("pause"));
  $("bulkResumeBtn").addEventListener("click", () => bulkTask("resume"));
  $("bulkCancelBtn").addEventListener("click", () => bulkTask("cancel"));
  $("savePromptBtn").addEventListener("click", savePrompt);
  $("resetPromptBtn").addEventListener("click", resetPrompt);
  $("saveFallbackBtn").addEventListener("click", saveFallbackConfig);
  $("saveModelBtn").addEventListener("click", saveModelConfig);
  checkSession().catch(err => console.error(err));
});

window.showDetail = showDetail;
window.deleteAlias = deleteAlias;
window.loadAccounts = loadAccounts;
window.setAccountStatus = setAccountStatus;
window.accountTimeline = accountTimeline;
window.jobDetail = jobDetail;
window.cancelJob = cancelJob;
window.planDetail = planDetail;
window.planAction = planAction;
window.toggleTask = toggleTask;
window.taskAction = taskAction;
window.toggleJob = toggleJob;
window.toggleAll = toggleAll;
window.editWorkerConfig = editWorkerConfig;
