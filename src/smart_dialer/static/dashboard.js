(() => {
  const lab = document.querySelector("[data-decision-lab]");
  if (!lab) return;

  const controls = {
    agents: lab.querySelector("[data-input='agents']"),
    answerRate: lab.querySelector("[data-input='answer-rate']"),
    risk: lab.querySelector("[data-input='risk']"),
  };
  const scenarios = {
    steady: {
      agents: 10, answerRate: 30, risk: 5, attempts: 100,
      ringing: 2, providerHealthy: true, rapidDrop: false,
    },
    cold: {
      agents: 10, answerRate: 30, risk: 5, attempts: 12,
      ringing: 2, providerHealthy: true, rapidDrop: false,
    },
    drop: {
      agents: 6, answerRate: 30, risk: 5, attempts: 100,
      ringing: 2, providerHealthy: true, rapidDrop: true,
    },
    degraded: {
      agents: 10, answerRate: 30, risk: 5, attempts: 100,
      ringing: 2, providerHealthy: false, rapidDrop: false,
    },
  };
  let state = { ...scenarios.steady };
  let requestSequence = 0;
  let debounceTimer;

  const field = (name) => lab.querySelector(`[data-output='${name}']`);
  const percentage = (value, digits = 1) => `${(value * 100).toFixed(digits)}%`;

  function syncControlLabels() {
    field("agents-input").textContent = controls.agents.value;
    field("answer-input").textContent = `${controls.answerRate.value}%`;
    field("risk-input").textContent = `${(Number(controls.risk.value) / 10).toFixed(2)}%`;
  }

  function setScenario(name) {
    state = { ...scenarios[name] };
    controls.agents.value = state.agents;
    controls.answerRate.value = state.answerRate;
    controls.risk.value = state.risk;
    lab.querySelectorAll("[data-scenario]").forEach((button) => {
      const active = button.dataset.scenario === name;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    syncControlLabels();
    evaluate();
  }

  function readControls() {
    state = {
      ...scenarios.steady,
      agents: Number(controls.agents.value),
      answerRate: Number(controls.answerRate.value),
      risk: Number(controls.risk.value),
    };
    lab.querySelectorAll("[data-scenario]").forEach((button) => {
      button.classList.remove("active");
      button.setAttribute("aria-pressed", "false");
    });
    syncControlLabels();
  }

  function makeQuery() {
    return new URLSearchParams({
      available_agents: String(state.agents),
      ringing_calls: String(state.ringing),
      observed_answers: String(Math.round(state.attempts * state.answerRate / 100)),
      observed_attempts: String(state.attempts),
      risk_tolerance: String(state.risk / 1000),
      provider_healthy: String(state.providerHealthy),
      rapid_agent_drop: String(state.rapidDrop),
    });
  }

  function renderParticles(requested, approved) {
    const stream = lab.querySelector("[data-particle-stream]");
    const count = Math.max(1, Math.min(requested, 22));
    const approvalRatio = requested ? approved / requested : 0;
    stream.replaceChildren();
    for (let index = 0; index < count; index += 1) {
      const dot = document.createElement("i");
      dot.className = index / count < approvalRatio ? "accepted" : "held";
      dot.style.setProperty("--i", String(index));
      stream.append(dot);
    }
  }

  function render(data) {
    const { proposal, receipt, inputs } = data;
    const fallback = receipt.effective_mode === "progressive";
    lab.dataset.state = fallback ? "fallback" : receipt.decision;
    field("proposal").textContent = proposal.requested_calls;
    field("approved").textContent = receipt.approved_calls;
    field("observed").textContent = percentage(
      inputs.observed_attempts ? inputs.observed_answers / inputs.observed_attempts : 0,
    );
    field("wilson").textContent = percentage(receipt.answer_rate_upper_bound);
    field("overload").textContent = percentage(receipt.overload_probability, 3);
    field("mode").textContent = receipt.effective_mode;
    field("decision").textContent = fallback
      ? "Progressive fallback"
      : `${receipt.decision} · ${receipt.approved_calls} calls`;
    field("reason").textContent = receipt.reasons.length
      ? receipt.reasons.join(" · ")
      : "Within the confidence-bounded overload limit";
    field("summary").textContent = fallback
      ? "Uncertainty or policy removed predictive permission. The system returned to one call per available human."
      : receipt.decision === "reduced"
        ? `The pacing engine proposed ${proposal.requested_calls}. Safety allowed ${receipt.approved_calls} before any reservation existed.`
        : "The proposal fits inside the operator-owned risk budget.";
    renderParticles(proposal.requested_calls, receipt.approved_calls);
  }

  async function evaluate() {
    const sequence = ++requestSequence;
    lab.classList.add("loading");
    lab.removeAttribute("data-error");
    try {
      const response = await fetch(`/v1/demo/pacing-decision?${makeQuery()}`);
      if (!response.ok) throw new Error(`Decision endpoint returned ${response.status}`);
      const data = await response.json();
      if (sequence === requestSequence) render(data);
    } catch (error) {
      if (sequence === requestSequence) {
        lab.dataset.error = "true";
        field("summary").textContent = "The decision endpoint is unavailable. Check the API health endpoint and retry.";
        field("reason").textContent = error.message;
      }
    } finally {
      if (sequence === requestSequence) lab.classList.remove("loading");
    }
  }

  lab.querySelectorAll("[data-scenario]").forEach((button) => {
    button.addEventListener("click", () => setScenario(button.dataset.scenario));
  });
  Object.values(controls).forEach((control) => {
    control.addEventListener("input", () => {
      readControls();
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(evaluate, 120);
    });
  });
  document.querySelector("[data-run-demo]")?.addEventListener("click", () => {
    lab.scrollIntoView({ behavior: "smooth", block: "center" });
    setScenario("steady");
  });
  document.querySelector("[data-refresh-page]")?.addEventListener("click", () => window.location.reload());

  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      revealObserver.unobserve(entry.target);
    });
  }, { threshold: 0.08 });
  document.querySelectorAll(".reveal").forEach((section) => revealObserver.observe(section));

  syncControlLabels();
  evaluate();
})();
