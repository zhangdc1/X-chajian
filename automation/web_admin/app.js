const state = {
  section: "dashboard",
  user: "",
  selectedTaskIds: new Set(),
  selectedJobIds: new Set(),
};

const titles = {
  dashboard: ["仪表盘", "系统健康、任务和异常集中视图"],
  workers: ["电脑管理", "在线状态、同步分组和客户端信息"],
  groups: ["分组账号", "别名、账号状态和分组同步管理"],
  jobs: ["任务中心", "排队、执行、失败、取消和抢占任务"],
  plans: ["账号评分", "评分计划、调度和中央提示词"],
  schedule: ["调度任务", "计划任务批量暂停、恢复和取消"],
  audit: ["审计设置", "后台操作记录和系统配置摘要"],
};

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

function statusPill(status) {
  const s = String(status || "-");
  const cls = ["completed", "active", "online", "scheduled", "auto_scheduled"].includes(s)
    ? "ok"
    : ["failed", "cancelled", "cancel_requested", "deleted", "inactive"].includes(s)
      ? "bad"
      : ["expired_missed", "paused", "preempted"].includes(s)
        ? "warn"
        : "blue";
  return `<span class="pill ${cls}">${esc(s)}</span>`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
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
  return `<div class="table-wrap"><table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function showDetail(title, data) {
  $("dialogTitle").textContent = title;
  $("dialogBody").textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  $("detailDialog").showModal();
}

function setSection(section) {
  state.section = section;
  document.querySelectorAll("#nav button").forEach(btn => btn.classList.toggle("active", btn.dataset.section === section));
  document.querySelectorAll(".section").forEach(el => el.classList.toggle("active", el.id === section));
  $("pageTitle").textContent = titles[section][0];
  $("pageSubtitle").textContent = titles[section][1];
  loadCurrent();
}

async function checkSession() {
  try {
    const data = await api("/admin/api/me");
    state.user = data.user;
    $("userBadge").textContent = data.user;
    $("loginView").classList.add("hidden");
    $("appView").classList.remove("hidden");
    await loadCurrent();
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
    await loadCurrent();
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
  $("statGrid").innerHTML = cards.map(([label, value]) => `<div class="stat-card"><div class="label">${label}</div><div class="value">${value}</div></div>`).join("");
  $("runningJobs").innerHTML = renderJobsMini(data.running_jobs || []);
  const issues = [
    ...(data.recent_failures || []).map(j => ({ type: "失败任务", text: `job=${j.id} ${j.job_type} ${j.error || ""}`, time: j.updated_at })),
    ...(data.missed_tasks || []).map(t => ({ type: "错过调度", text: `task=${t.id} plan=${t.plan_id || "-"} ${t.last_error || ""}`, time: t.updated_at })),
    ...(data.workers || []).filter(w => !w.online).map(w => ({ type: "离线电脑", text: `${w.node_id} ${w.label || ""} 已离线 ${w.offline_seconds || 0} 秒`, time: w.last_seen })),
  ].slice(0, 20);
  $("todayIssues").innerHTML = table(["类型", "说明", "时间"], issues.map(i => `<tr><td>${esc(i.type)}</td><td class="wrap">${esc(i.text)}</td><td>${fmtTime(i.time)}</td></tr>`), "今日暂无异常");
}

function renderJobsMini(jobs) {
  return table(["ID", "电脑", "类型", "状态", "开始/更新"], jobs.map(j => `
    <tr>
      <td>${j.id}</td>
      <td>${esc(j.leased_by || j.target_node_id || "-")}</td>
      <td>${esc(j.job_type)}${j.priority >= 100 ? ' <span class="pill blue">最高优先级</span>' : ""}</td>
      <td>${statusPill(j.status)}</td>
      <td>${fmtTime(j.updated_at)}</td>
    </tr>`), "暂无执行中任务");
}

async function loadWorkers() {
  const data = await api("/admin/api/workers?limit=200");
  $("workersTable").innerHTML = table(["电脑", "标签", "在线", "Grok", "同步分组", "最后心跳", "版本", "操作"], data.workers.map(w => {
    const meta = w.meta || {};
    const groups = meta.sync_group_ids || meta.group_ids || meta.groups || [];
    return `<tr>
      <td>${esc(w.node_id)}</td>
      <td>${esc(w.label)}</td>
      <td>${w.online ? statusPill("online") : statusPill("offline")}</td>
      <td>${meta.enable_grok_browser || meta.grok_enabled ? statusPill("开启") : statusPill("关闭")}</td>
      <td class="wrap">${esc(Array.isArray(groups) ? groups.join(", ") : groups)}</td>
      <td>${fmtTime(w.last_seen)}<div class="small muted">${w.offline_seconds || 0} 秒前</div></td>
      <td>${esc(meta.app_version || meta.version || "-")}</td>
      <td><button class="ghost" onclick='showDetail("电脑详情", ${JSON.stringify(w).replaceAll("'", "&#39;")})'>详情</button></td>
    </tr>`;
  }));
}

async function loadGroups() {
  const data = await api("/admin/api/groups?limit=300");
  $("groupsTable").innerHTML = table(["别名", "group_id", "active", "inactive", "来源电脑", "同步电脑", "历史别名", "操作"], data.groups.map(g => `
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
  $("accountsTable").innerHTML = table(["账号", "profile_id", "分组", "电脑", "状态", "最后同步", "操作"], data.accounts.map(a => `
    <tr>
      <td>${esc(a.profile_name || a.x_username || "-")}</td>
      <td>${esc(a.profile_id)}</td>
      <td>${esc(a.group_id || "-")}</td>
      <td>${esc(a.node_id || "-")}</td>
      <td>${statusPill(a.status)}</td>
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
  const query = new URLSearchParams();
  if ($("jobStatus").value) query.set("status", $("jobStatus").value);
  if ($("jobNode").value.trim()) query.set("node_id", $("jobNode").value.trim());
  query.set("limit", "300");
  const data = await api(`/admin/api/jobs?${query.toString()}`);
  $("jobsTable").innerHTML = table(["ID", "类型", "模式", "优先级", "电脑", "状态", "错误", "更新", "操作"], data.jobs.map(j => `
    <tr>
      <td>${j.id}</td>
      <td>${esc(j.job_type)}</td>
      <td>${esc((j.payload || {}).mode || "-")}</td>
      <td>${j.priority || 10}${(j.priority || 0) >= 100 ? ' <span class="pill blue">最高</span>' : ""}</td>
      <td>${esc(j.leased_by || j.target_node_id || "-")}</td>
      <td>${statusPill(j.status)}</td>
      <td class="wrap">${esc(j.error || "")}</td>
      <td>${fmtTime(j.updated_at)}</td>
      <td>
        <button class="ghost" onclick="jobDetail(${j.id})">日志</button>
        ${["queued", "leased"].includes(j.status) ? `<button class="ghost danger" onclick="cancelJob(${j.id})">取消</button>` : ""}
      </td>
    </tr>`));
}

async function loadPlans() {
  const query = new URLSearchParams();
  if ($("planGroup").value.trim()) query.set("group_id", $("planGroup").value.trim());
  query.set("limit", "300");
  const data = await api(`/admin/api/score-plans?${query.toString()}`);
  $("plansTable").innerHTML = table(["ID", "账号", "分组", "评分", "状态", "来源", "调度", "创建", "操作"], data.plans.map(p => `
    <tr>
      <td>${p.id}</td>
      <td>${esc(p.account_id)}</td>
      <td>${esc(p.group_id || "-")}</td>
      <td>${esc(p.score ?? "-")}</td>
      <td>${statusPill(p.status)}</td>
      <td>${esc(p.source_job_id || "-")}</td>
      <td>${esc(JSON.stringify(p.task_summary || {}))}</td>
      <td>${fmtTime(p.created_at)}</td>
      <td>
        <button class="ghost" onclick="planDetail(${p.id})">详情</button>
        <button class="ghost" onclick="planAction(${p.id}, 'pause')">暂停</button>
        <button class="ghost" onclick="planAction(${p.id}, 'resume')">恢复</button>
        <button class="ghost danger" onclick="planAction(${p.id}, 'delete')">删除</button>
      </td>
    </tr>`));
  await loadPrompt();
}

async function loadPrompt() {
  const data = await api("/admin/api/score-prompt");
  $("scorePrompt").value = data.prompt || "";
}

async function loadSchedule() {
  state.selectedTaskIds.clear();
  const query = new URLSearchParams();
  if ($("taskStatus").value) query.set("status", $("taskStatus").value);
  query.set("limit", "700");
  const data = await api(`/admin/api/schedule?${query.toString()}`);
  $("scheduleTable").innerHTML = table(["选择", "ID", "执行时间", "账号", "模式", "状态", "plan/job", "错误", "操作"], data.tasks.map(t => `
    <tr>
      <td><input type="checkbox" onchange="toggleTask(${t.id}, this.checked)"></td>
      <td>${t.id}</td>
      <td>${fmtTime(t.run_at)}</td>
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
  $("auditTable").innerHTML = table(["ID", "管理员", "动作", "目标", "结果", "时间"], audit.logs.map(l => `
    <tr>
      <td>${l.id}</td>
      <td>${esc(l.actor)}</td>
      <td>${esc(l.action)}</td>
      <td>${esc(l.target_type)}:${esc(l.target_id)}</td>
      <td>${l.ok ? statusPill("ok") : statusPill("failed")}</td>
      <td>${fmtTime(l.created_at)}</td>
    </tr>`));
  $("settingsBox").innerHTML = `<div class="table-wrap"><table><tbody>${
    Object.entries(settings.settings || {}).map(([k, v]) => `<tr><th>${esc(k)}</th><td class="wrap">${esc(v)}</td></tr>`).join("")
  }</tbody></table></div>`;
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
  await loadGroups();
}

async function deleteAlias(alias) {
  if (!confirm(`确认解绑别名 ${alias}？`)) return;
  await api("/admin/api/groups/alias/delete", { method: "POST", body: JSON.stringify({ alias }) });
  await loadGroups();
}

async function addSyncGroup() {
  const node_id = $("syncNodeId").value.trim();
  const group_id = $("syncGroupId").value.trim();
  if (!node_id || !group_id) return alert("请填写 node_id 和 group_id");
  await api("/admin/api/worker-sync-groups", { method: "POST", body: JSON.stringify({ node_id, group_id }) });
  await loadWorkers();
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
  showDetail(`评分计划 ${planId}`, data);
}

async function planAction(planId, action) {
  if (["delete", "cancel-all"].includes(action) && !confirm(`确认对计划 ${planId} 执行 ${action}？`)) return;
  await api(`/admin/api/score-plans/${planId}/${action}`, { method: "POST", body: "{}" });
  await loadPlans();
}

function toggleTask(taskId, checked) {
  if (checked) state.selectedTaskIds.add(taskId);
  else state.selectedTaskIds.delete(taskId);
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

async function loadWorkers() {
  const data = await api("/admin/api/workers?limit=200");
  $("workersTable").innerHTML = table(["电脑", "标签", "在线", "Grok", "同步分组", "当前任务", "最后心跳", "操作"], data.workers.map(w => {
    const cfg = w.central_config || {};
    const groups = cfg.sync_group_ids || [];
    const current = (w.meta || {}).current_job || {};
    return `<tr>
      <td>${esc(w.node_id)}</td>
      <td>${esc(w.label)}</td>
      <td>${w.online ? statusPill("online") : statusPill("offline")}</td>
      <td>${cfg.enable_grok_browser ? statusPill("开启") : statusPill("关闭")}</td>
      <td class="wrap">${esc(Array.isArray(groups) ? groups.join(", ") : groups)}</td>
      <td class="wrap">${current.id ? `job=${esc(current.id)} ${esc(current.job_type || "")}` : "-"}</td>
      <td>${fmtTime(w.last_seen)}<div class="small muted">${w.offline_seconds || 0} 秒前</div></td>
      <td>
        <button class="ghost" onclick="editWorkerConfig('${esc(w.node_id)}')">配置</button>
        <button class="ghost" onclick='showDetail("电脑详情", ${JSON.stringify(w).replaceAll("'", "&#39;")})'>详情</button>
      </td>
    </tr>`;
  }));
}

async function loadAccounts(groupId = "") {
  if (groupId) $("accountFilter").value = groupId;
  const query = new URLSearchParams();
  const filter = $("accountFilter").value.trim();
  if (filter) query.set("group_id", filter);
  query.set("include_inactive", $("includeInactive").checked ? "1" : "0");
  query.set("limit", "1000");
  const data = await api(`/admin/api/accounts?${query.toString()}`);
  $("accountsTable").innerHTML = table(["账号", "profile_id", "分组", "电脑", "账号状态", "电脑在线", "最后同步", "操作"], data.accounts.map(a => `
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
  if ($("jobNode").value.trim()) query.set("node_id", $("jobNode").value.trim());
  query.set("limit", "300");
  const data = await api(`/admin/api/jobs?${query.toString()}`);
  $("jobsTable").innerHTML = table(["选择", "ID", "类型", "模式", "优先级", "电脑", "状态", "错误", "更新", "操作"], data.jobs.map(j => `
    <tr>
      <td><input type="checkbox" onchange="toggleJob(${j.id}, this.checked)"></td>
      <td>${j.id}</td>
      <td>${esc(j.job_type)}</td>
      <td>${esc((j.payload || {}).mode || "-")}</td>
      <td>${j.priority || 10}${(j.priority || 0) >= 100 ? ' <span class="pill blue">最高</span>' : ""}</td>
      <td>${esc(j.leased_by || j.target_node_id || "-")}</td>
      <td>${statusPill(j.status)}</td>
      <td class="wrap">${esc(j.error || "")}</td>
      <td>${fmtTime(j.updated_at)}</td>
      <td>
        <button class="ghost" onclick="jobDetail(${j.id})">日志</button>
        ${["queued", "leased"].includes(j.status) ? `<button class="ghost danger" onclick="cancelJob(${j.id})">取消</button>` : ""}
      </td>
    </tr>`));
}

async function loadPlans() {
  const query = new URLSearchParams();
  if ($("planGroup").value.trim()) query.set("group_id", $("planGroup").value.trim());
  query.set("limit", "300");
  const data = await api(`/admin/api/score-plans?${query.toString()}`);
  $("plansTable").innerHTML = table(["ID", "账号", "分组", "评分", "状态", "来源", "调度", "创建", "操作"], data.plans.map(p => `
    <tr>
      <td>${p.id}</td>
      <td>${esc(p.account_display || p.account_name || p.account_id)}</td>
      <td>${esc(p.group_id || "-")}</td>
      <td>${esc(p.score ?? "-")}</td>
      <td>${statusPill(p.status)}</td>
      <td>${esc(p.source_job_id || "-")}</td>
      <td>${esc(JSON.stringify(p.task_summary || {}))}</td>
      <td>${fmtTime(p.created_at)}</td>
      <td>
        <button class="ghost" onclick="planDetail(${p.id})">详情</button>
        <button class="ghost" onclick="planAction(${p.id}, 'pause')">暂停</button>
        <button class="ghost" onclick="planAction(${p.id}, 'resume')">恢复</button>
        <button class="ghost danger" onclick="planAction(${p.id}, 'delete')">删除</button>
      </td>
    </tr>`));
  await loadPrompt();
}

function toggleJob(jobId, checked) {
  if (checked) state.selectedJobIds.add(jobId);
  else state.selectedJobIds.delete(jobId);
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
  await loadJobs();
}

async function editWorkerConfig(nodeId) {
  const data = await api(`/admin/api/workers/${encodeURIComponent(nodeId)}/config`);
  const cfg = data.config || {};
  const label = prompt("电脑标签", cfg.label || nodeId);
  if (label === null) return;
  const grok = confirm("是否开启 Grok 浏览器？点击取消则关闭。");
  await api(`/admin/api/workers/${encodeURIComponent(nodeId)}/config`, {
    method: "POST",
    body: JSON.stringify({ label, enable_grok_browser: grok, open_gui_for_legacy: false }),
  });
  await loadWorkers();
}

document.addEventListener("DOMContentLoaded", () => {
  $("loginForm").addEventListener("submit", login);
  $("logoutBtn").addEventListener("click", logout);
  $("refreshBtn").addEventListener("click", () => loadCurrent().catch(err => alert(err.message)));
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
window.bulkCancelJobs = bulkCancelJobs;
window.cleanupStaleJobs = cleanupStaleJobs;
window.editWorkerConfig = editWorkerConfig;
