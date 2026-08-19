(() => {
  "use strict";

  const API = "/api/v1";

  const el = (id) => document.getElementById(id);

  const pileSelect = el("pile-select");
  const createPileForm = el("create-pile-form");
  const pileSummary = el("pile-summary");
  const activePileName = el("active-pile-name");
  const activePileDir = el("active-pile-dir");

  const understandSection = el("understand-section");
  const runUnderstandBtn = el("run-understand-btn");
  const runScanBtn = el("run-scan-btn");
  const understandStatus = el("understand-status");

  const registerSection = el("register-section");
  const registerMeta = el("register-meta");
  const registerBody = el("register-body");
  const registerActions = el("register-actions");
  const approveRegisterBtn = el("approve-register-btn");
  const rejectRegisterBtn = el("reject-register-btn");

  const rulesSection = el("rules-section");
  const rulesList = el("rules-list");
  const addRuleForm = el("add-rule-form");
  const ruleTextInput = el("rule-text");

  const examineSection = el("examine-section");
  const runExamineBtn = el("run-examine-btn");
  const examineStatus = el("examine-status");

  const findingsSection = el("findings-section");
  const findingsList = el("findings-list");
  const submitReviewBtn = el("submit-review-btn");

  const errorSection = el("error-section");
  const errorMessage = el("error-message");
  const errorDismiss = el("error-dismiss");

  let activePileId = null;
  let currentRegisterVersion = null;
  let currentRegisterStatus = null;

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value === null || value === undefined ? "" : String(value);
    return div.innerHTML;
  }

  function showError(message) {
    errorMessage.textContent = message;
    errorSection.classList.remove("hidden");
  }

  function clearError() {
    errorSection.classList.add("hidden");
    errorMessage.textContent = "";
  }

  async function apiGet(path) {
    const response = await fetch(API + path);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `GET ${path} failed (${response.status})`);
    return data;
  }

  async function apiPost(path, body) {
    const response = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `POST ${path} failed (${response.status})`);
    return data;
  }

  async function loadPiles() {
    try {
      const piles = await apiGet("/piles");
      pileSelect.innerHTML = '<option value="">-- select a pile --</option>';
      piles.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.pile_id;
        opt.textContent = `${p.name} (${p.pile_id.slice(0, 8)})`;
        opt.dataset.name = p.name;
        opt.dataset.dir = p.watch_dir;
        pileSelect.appendChild(opt);
      });
    } catch (err) {
      showError(err.message);
    }
  }

  pileSelect.addEventListener("change", () => {
    const opt = pileSelect.selectedOptions[0];
    if (!opt || !opt.value) {
      activePileId = null;
      pileSummary.classList.add("hidden");
      [understandSection, registerSection, rulesSection, examineSection, findingsSection].forEach((s) =>
        s.classList.add("hidden")
      );
      return;
    }
    selectPile(opt.value, opt.dataset.name, opt.dataset.dir);
  });

  function selectPile(pileId, name, dir) {
    activePileId = pileId;
    clearError();
    activePileName.textContent = name || pileId;
    activePileDir.textContent = dir || "";
    pileSummary.classList.remove("hidden");
    understandSection.classList.remove("hidden");
    rulesSection.classList.remove("hidden");
    examineSection.classList.remove("hidden");

    loadRegister();
    loadRules();
    loadFindings();
  }

  createPileForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearError();
    const name = el("pile-name").value.trim();
    const sourceDir = el("pile-source-dir").value.trim();
    if (!name || !sourceDir) {
      showError("Provide both a name and a source directory.");
      return;
    }
    try {
      const pile = await apiPost("/piles", { name, source_dir: sourceDir });
      await loadPiles();
      pileSelect.value = pile.pile_id;
      selectPile(pile.pile_id, name, sourceDir);
      el("create-pile-details").removeAttribute("open");
      createPileForm.reset();
    } catch (err) {
      showError(err.message);
    }
  });

  runUnderstandBtn.addEventListener("click", async () => {
    if (!activePileId) return;
    clearError();
    runUnderstandBtn.disabled = true;
    understandStatus.textContent = "Running understand...";
    try {
      const status = await apiPost(`/piles/${activePileId}/understand`);
      understandStatus.textContent = `Run ${status.status}. Stages: ${status.stages
        .map((s) => `${s.stage_name}=${s.status}`)
        .join(", ")}`;
      await loadRegister();
    } catch (err) {
      showError(err.message);
      understandStatus.textContent = "";
    } finally {
      runUnderstandBtn.disabled = false;
    }
  });

  runScanBtn.addEventListener("click", async () => {
    if (!activePileId) return;
    clearError();
    runScanBtn.disabled = true;
    understandStatus.textContent = "Scanning for new documents...";
    try {
      const status = await apiPost(`/piles/${activePileId}/watch/scan`);
      // get_run_status() exposes each stage's decision but not its
      // full result payload - "complete_early" on the first stage is
      // the reliable signal for "scanned, found nothing new," not a
      // result field that was never actually part of the API shape.
      const noNewDocs = status.stages[0]?.decision === "complete_early";
      understandStatus.textContent = noNewDocs
        ? "Scan complete: no new documents found (short-circuited, nothing recomputed)."
        : `Scan complete: ${status.status}.`;
      await loadRegister();
    } catch (err) {
      showError(err.message);
      understandStatus.textContent = "";
    } finally {
      runScanBtn.disabled = false;
    }
  });

  async function loadRegister() {
    if (!activePileId) return;
    try {
      const data = await apiGet(`/piles/${activePileId}/register`);
      renderRegister(data);
    } catch (err) {
      registerSection.classList.add("hidden");
    }
  }

  function statusBadge(status) {
    const cls = status === "approved" ? "badge-approved" : status === "rejected" ? "badge-rejected" : "badge-pending";
    return `<span class="badge ${cls}">${escapeHtml(status)}</span>`;
  }

  function renderRegister(data) {
    currentRegisterVersion = data.version_number;
    currentRegisterStatus = data.status;

    registerMeta.innerHTML = `
      <p>Version ${data.version_number} ${statusBadge(data.status)}</p>
      <p class="hint">${escapeHtml(data.change_summary || "")}</p>
    `;

    const vendors = data.content.vendors || [];
    registerBody.innerHTML = vendors
      .map((v) => {
        const disagreementsHtml = (v.disagreements || [])
          .map(
            (d) =>
              `<div class="disagreement-item"><strong>${escapeHtml(
                d.type.replace(/_/g, " ")
              )}</strong>: ${escapeHtml(d.description)}</div>`
          )
          .join("") || `<p class="hint">No disagreements.</p>`;

        return `<div class="vendor-block">
          <h3>${escapeHtml(v.vendor)} <span class="hint">(${escapeHtml(v.contract_no)})</span></h3>
          <table>
            <tr><th>Current NTE</th><th>Invoices</th><th>Total invoiced</th></tr>
            <tr>
              <td>${v.current_not_to_exceed_amount != null ? "$" + v.current_not_to_exceed_amount.toLocaleString() : "-"}</td>
              <td>${v.invoice_count}</td>
              <td>$${(v.total_invoiced || 0).toLocaleString()}</td>
            </tr>
          </table>
          ${disagreementsHtml}
        </div>`;
      })
      .join("");

    const injections = data.content.embedded_instructions_detected || [];
    if (injections.length) {
      registerBody.innerHTML += `<div class="vendor-block">
        <h3>Embedded instructions detected (not acted on)</h3>
        ${injections
          .map(
            (n) =>
              `<div class="disagreement-item"><strong>${escapeHtml(n.vendor)}</strong> (${escapeHtml(
                n.filename
              )}): ${escapeHtml(n.instruction_text)}</div>`
          )
          .join("")}
      </div>`;
    }

    registerSection.classList.remove("hidden");
    if (data.status === "pending") {
      registerActions.classList.remove("hidden");
    } else {
      registerActions.classList.add("hidden");
    }
  }

  approveRegisterBtn.addEventListener("click", () => decideRegister("approve"));
  rejectRegisterBtn.addEventListener("click", () => decideRegister("reject"));

  async function decideRegister(decision) {
    if (!activePileId || currentRegisterVersion == null) return;
    clearError();
    try {
      await apiPost(`/piles/${activePileId}/register/${currentRegisterVersion}/${decision}`, {
        decided_by: "reviewer",
      });
      await loadRegister();
    } catch (err) {
      showError(err.message);
    }
  }

  async function loadRules() {
    if (!activePileId) return;
    try {
      const rules = await apiGet(`/piles/${activePileId}/rules`);
      rulesList.innerHTML = rules.length
        ? rules.map((r) => `<div class="rule-item">${escapeHtml(r.rule_text)}</div>`).join("")
        : `<p class="hint">No rules yet - add one below.</p>`;
    } catch (err) {
      showError(err.message);
    }
  }

  addRuleForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!activePileId) return;
    clearError();
    const text = ruleTextInput.value.trim();
    if (!text) return;
    try {
      await apiPost(`/piles/${activePileId}/rules`, { rule_text: text });
      ruleTextInput.value = "";
      await loadRules();
    } catch (err) {
      showError(err.message);
    }
  });

  runExamineBtn.addEventListener("click", async () => {
    if (!activePileId) return;
    clearError();
    runExamineBtn.disabled = true;
    examineStatus.textContent = "Running examine...";
    try {
      const status = await apiPost(`/piles/${activePileId}/examine`);
      examineStatus.textContent = `Run ${status.status}. ${status.stages.length} stage(s) (one per active rule, plus compile).`;
      await loadFindings();
    } catch (err) {
      showError(err.message);
      examineStatus.textContent = "";
    } finally {
      runExamineBtn.disabled = false;
    }
  });

  async function loadFindings() {
    if (!activePileId) return;
    try {
      const findings = await apiGet(`/piles/${activePileId}/findings`);
      renderFindings(findings);
    } catch (err) {
      showError(err.message);
    }
  }

  function renderFindings(findings) {
    if (!findings.length) {
      findingsSection.classList.add("hidden");
      return;
    }
    findingsSection.classList.remove("hidden");

    findingsList.innerHTML = findings
      .map((f) => {
        const decidedClass =
          f.status === "approved" ? "decided-approved" : f.status === "rejected" ? "decided-rejected" : "";
        const controls =
          f.status === "pending"
            ? `<div class="finding-decision-controls">
                <label><input type="radio" name="decision-${f.finding_id}" value="approve" /> Approve</label>
                <label><input type="radio" name="decision-${f.finding_id}" value="reject" /> Reject</label>
                <label><input type="radio" name="decision-${f.finding_id}" value="" checked /> Skip for now</label>
              </div>`
            : "";

        return `<div class="finding-item ${decidedClass}" data-finding-id="${f.finding_id}">
          <div class="finding-header">
            <strong>${escapeHtml(f.title)}</strong>
            ${statusBadge(f.status)}
          </div>
          <p class="finding-desc">${escapeHtml(f.description)}</p>
          ${controls}
        </div>`;
      })
      .join("");

    const anyPending = findings.some((f) => f.status === "pending");
    submitReviewBtn.classList.toggle("hidden", !anyPending);
  }

  submitReviewBtn.addEventListener("click", async () => {
    if (!activePileId) return;
    clearError();

    const decisions = [];
    document.querySelectorAll(".finding-item").forEach((item) => {
      const findingId = item.dataset.findingId;
      const checked = item.querySelector(`input[type="radio"]:checked`);
      if (checked && checked.value) {
        decisions.push({ finding_id: findingId, decision: checked.value });
      }
    });

    if (!decisions.length) {
      showError("Select approve or reject for at least one finding before submitting.");
      return;
    }

    try {
      await apiPost(`/piles/${activePileId}/findings/review`, { decisions, decided_by: "reviewer" });
      await loadFindings();
    } catch (err) {
      showError(err.message);
    }
  });

  errorDismiss.addEventListener("click", clearError);

  loadPiles();
})();
