(() => {
  "use strict";
  const json = async (response) => {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  };
  const bootstrap = document.querySelector("#bootstrap-form");
  if (bootstrap) {
    bootstrap.addEventListener("submit", async (event) => {
      event.preventDefault();
      const capability = document.querySelector("#capability").value;
      try {
        await json(await fetch("/bootstrap", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({capability})
        }));
        window.location.replace("/");
      } catch (error) { alert(error.message); }
    });
    return;
  }

  const csrf = document.querySelector('meta[name="reportmachine-csrf"]').content;
  const headers = (extra = {}) => ({"Content-Type": "application/json", "X-CSRF-Token": csrf, ...extra});
  const message = document.querySelector("#message");
  let executionId = null;
  let ownerLeaseId = null;

  const replaceDefinitionList = (element, rows) => {
    element.replaceChildren();
    rows.forEach(([label, value]) => {
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = value == null ? "—" : String(value);
      element.append(term, detail);
    });
  };

  const renderScope = (run) => {
    const target = document.querySelector("#scope-summary");
    const intent = run && run.artifacts && run.artifacts.intent_contract;
    if (!intent || intent.state !== "available") {
      replaceDefinitionList(target, [["상태", intent ? intent.state : "unavailable"]]);
      return;
    }
    const normalized = intent.normalized_scope || {};
    replaceDefinitionList(target, [
      ["원문", intent.original_request],
      ["정규화 질문", normalized.primary_question],
      ["포함", (normalized.must_include || []).join(" / ")],
      ["제외", (normalized.must_not_include || []).join(" / ")],
      ["material scope delta", JSON.stringify(intent.scope_delta || [])],
      ["scope 상태", intent.scope_status],
      ["승인 상태", intent.approval_status],
      ["현재 scope SHA-256", intent.current_scope_sha256]
    ]);
    const scopeInput = document.querySelector('input[name="scope_sha256"]');
    if (intent.scope_status === "approval_required" && intent.current_scope_sha256) {
      scopeInput.value = intent.current_scope_sha256;
    }
  };

  const renderArtifacts = (run) => {
    const target = document.querySelector("#artifacts");
    target.replaceChildren();
    if (!run || !run.run_id || !run.article || run.article.state === "not_checked") {
      target.textContent = "writer가 안정된 terminal/recovery 상태가 아니므로 열람하지 않습니다.";
      return;
    }
    const names = [
      ["article", "기사"], ["article_result", "기사 결과"],
      ["preflight", "preflight"], ["final_sync", "final sync"],
      ["run_manifest", "run manifest"], ["source_ledger", "source ledger"],
      ["image_ledger", "image ledger"], ["claim_ledger", "claim ledger"],
      ["paragraph_map", "paragraph map"], ["research_appendix", "research appendix"],
      ["publish_pdf", "발행 PDF"], ["summary_image", "요약 이미지"]
    ];
    names.forEach(([name, label]) => {
      const link = document.createElement("a");
      link.href = `/api/artifacts/${encodeURIComponent(run.run_id)}/${name}`;
      link.textContent = label;
      target.append(link);
    });
  };

  const renderStatus = (payload) => {
    document.querySelector("#status").textContent = JSON.stringify(payload, null, 2);
    const execution = payload.execution || {};
    const run = payload.run || null;
    const operation = run && run.operation ? run.operation : execution;
    const article = run && run.article ? run.article : {};
    const writer = run && run.writer ? run.writer : {};
    executionId = execution.execution_id || null;
    ownerLeaseId = writer.owner && writer.owner.lease_id ? writer.owner.lease_id : null;
    replaceDefinitionList(document.querySelector("#status-summary"), [
      ["run_id", run && run.run_id],
      ["운영 상태", operation && operation.state],
      ["요청 phase", operation && operation.requested_phase],
      ["프로세스 결과", operation && operation.process_outcome],
      ["exit code", operation && operation.exit_code],
      ["이번 실행 이후 변경 증거", (operation && operation.changed_artifacts_since_start || []).join(", ")],
      ["writer", writer.state],
      ["기사 artifact 상태", article.state],
      ["기사 유효", article.valid],
      ["latest 승격", run && run.latest && run.latest.member]
    ]);
    renderScope(run);
    renderArtifacts(run);
  };

  const refresh = async () => {
    try {
      const [status, log] = await Promise.all([
        json(await fetch("/api/status")),
        json(await fetch("/api/log"))
      ]);
      renderStatus(status);
      document.querySelector("#log").textContent = log.log || "";
    } catch (error) { message.textContent = error.message; }
  };

  document.querySelector("#action-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const payload = Object.fromEntries(fields.entries());
    payload.confirm_rerun = fields.get("confirm_rerun") === "on";
    if (!["prepare", "smoke", "run_all"].includes(payload.action) || !payload.topic) delete payload.topic;
    if (payload.action !== "allowed_phase") delete payload.phase;
    if (payload.action !== "approve_scope") delete payload.scope_sha256;
    try {
      await json(await fetch("/api/action", {
        method: "POST",
        headers: headers({"Idempotency-Key": crypto.randomUUID()}),
        body: JSON.stringify(payload)
      }));
      message.textContent = "Action accepted.";
      refresh();
    } catch (error) { message.textContent = error.message; }
  });

  document.querySelector("#cancel").addEventListener("click", async () => {
    if (!executionId) { message.textContent = "No active execution."; return; }
    try {
      await json(await fetch("/api/cancel", {
        method: "POST", headers: headers(), body: JSON.stringify({execution_id: executionId})
      }));
      refresh();
    } catch (error) { message.textContent = error.message; }
  });

  document.querySelector("#recover").addEventListener("click", async () => {
    if (!executionId) { message.textContent = "No recovery receipt."; return; }
    const payload = {execution_id: executionId};
    if (ownerLeaseId) payload.owner_lease_id = ownerLeaseId;
    try {
      await json(await fetch("/api/recovery", {
        method: "POST", headers: headers(), body: JSON.stringify(payload)
      }));
      message.textContent = "Recovery state acknowledged. No workflow was retried.";
      refresh();
    } catch (error) { message.textContent = error.message; }
  });

  refresh();
  window.setInterval(refresh, 3000);
})();
