// Social Media Asset Studio — client
(() => {
  const api = {
    create:     (brief) => fetchJSON("/api/social/projects", { method: "POST", body: brief }),
    research:   (id)    => fetchJSON(`/api/social/projects/${id}/research`, { method: "POST" }),
    style:      (id)    => fetchJSON(`/api/social/projects/${id}/style-guide`, { method: "POST" }),
    mockups:    (id, body) => fetchJSON(`/api/social/projects/${id}/mockups`, { method: "POST", body }),
    vote:       (id, body) => fetchJSON(`/api/social/projects/${id}/vote`, { method: "POST", body }),
    updateMockup: (id, mid, body) => fetchJSON(`/api/social/projects/${id}/mockups/${mid}`, { method: "PATCH", body }),
    regenerate: (id, mid, body)   => fetchJSON(`/api/social/projects/${id}/mockups/${mid}/regenerate`, { method: "POST", body: body || {} }),
    build:      (id, body) => fetchJSON(`/api/social/projects/${id}/build`, { method: "POST", body }),
  };

  const state = {
    projectId: null,
    research: null,
    styleGuide: null,
    mockups: [],
    votes: new Map(), // mockup_id -> { value, heart, reject }
    refinePicks: [],
    originalConcepts: new Map(), // mockup_id -> snapshot of text fields
  };

  // ---------------------------------------------------------------------
  // Step orchestration
  // ---------------------------------------------------------------------
  const STEPS = ["brief", "research", "style", "collage", "vote", "refine", "build"];
  const doneSteps = new Set();
  function go(step) {
    STEPS.forEach((s) => {
      document.querySelector(`.step[data-step="${s}"]`).hidden = s !== step;
      const li = document.querySelector(`.stepper__list li[data-step="${s}"]`);
      li.classList.toggle("is-active", s === step);
      li.classList.toggle("is-done", doneSteps.has(s) && s !== step);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function markDone(step) { doneSteps.add(step); }

  // ---------------------------------------------------------------------
  // Step 1: brief
  // ---------------------------------------------------------------------
  document.getElementById("briefForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const platforms = [...document.querySelectorAll("#platformChips input:checked")].map((i) => i.value);
    const handlesRaw = (fd.get("social_handles") || "").trim();
    const social_handles = {};
    handlesRaw.split(",").map((p) => p.trim()).filter(Boolean).forEach((p) => {
      const [k, v] = p.split(":").map((x) => x && x.trim());
      if (k && v) social_handles[k] = v;
    });
    const brief = {
      company_name: fd.get("company_name"),
      website: fd.get("website") || null,
      industry: fd.get("industry") || null,
      goals: fd.get("goals"),
      strategy: fd.get("strategy") || null,
      target_audience: fd.get("target_audience") || null,
      tone: fd.get("tone") || null,
      campaign_theme: fd.get("campaign_theme") || null,
      social_handles,
      platforms: platforms.length ? platforms : ["instagram_square"],
    };

    try {
      setButtonBusy(e.target.querySelector("button[type=submit]"), true, "Creating project…");
      const created = await api.create(brief);
      state.projectId = created.project_id;
      markDone("brief");
      go("research");
      await runResearchFlow();
    } catch (err) {
      alert("Failed to start: " + err.message);
    } finally {
      setButtonBusy(e.target.querySelector("button[type=submit]"), false, "Start research →");
    }
  });

  // ---------------------------------------------------------------------
  // Step 2: research
  // ---------------------------------------------------------------------
  async function runResearchFlow() {
    document.getElementById("researchLoader").hidden = false;
    document.getElementById("researchReport").hidden = true;
    const statuses = [
      "Searching the web…",
      "Scanning website…",
      "Reading social profiles…",
      "Synthesizing findings…",
    ];
    const statusEl = document.getElementById("researchStatus");
    let i = 0;
    const tick = setInterval(() => { statusEl.textContent = statuses[(++i) % statuses.length]; }, 1800);
    try {
      const r = await api.research(state.projectId);
      clearInterval(tick);
      state.research = r;
      renderResearch(r);
      document.getElementById("researchLoader").hidden = true;
      document.getElementById("researchReport").hidden = false;
    } catch (err) {
      clearInterval(tick);
      alert("Research failed: " + err.message);
    }
  }

  function renderResearch(r) {
    document.getElementById("rSummary").textContent = r.summary || "";
    document.getElementById("rPositioning").textContent = r.positioning || "—";
    document.getElementById("rAudience").textContent = r.audience_insights || "—";
    document.getElementById("rChannels").textContent = r.current_channel_observations || "—";
    fillList("rOpportunities", r.opportunities || []);
    fillList("rThemes", r.content_themes_observed || []);
    fillList("rCompetitors", r.competitors || []);
    const src = document.getElementById("rSources");
    src.innerHTML = "";
    (r.sources || []).forEach((s) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = s.url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = s.title || s.url;
      li.appendChild(a);
      src.appendChild(li);
    });
    if (!(r.sources || []).length) {
      src.innerHTML = '<li class="muted">(offline / no Google Search grounding — add GEMINI_API_KEY to enable)</li>';
    }
  }

  document.getElementById("goStyleBtn").addEventListener("click", async () => {
    markDone("research");
    go("style");
    await runStyleFlow();
  });

  // ---------------------------------------------------------------------
  // Step 3: style guide
  // ---------------------------------------------------------------------
  async function runStyleFlow() {
    document.getElementById("styleLoader").hidden = false;
    document.getElementById("styleView").hidden = true;
    try {
      const sg = await api.style(state.projectId);
      state.styleGuide = sg;
      renderStyleGuide(sg);
      document.getElementById("styleLoader").hidden = true;
      document.getElementById("styleView").hidden = false;
    } catch (err) {
      alert("Style guide failed: " + err.message);
    }
  }

  function renderStyleGuide(sg) {
    document.getElementById("sEssence").textContent = sg.brand_essence || "";
    document.getElementById("sVoice").textContent = sg.voice || "";
    const tone = document.getElementById("sTone");
    tone.innerHTML = "";
    (sg.tone_attributes || []).forEach((t) => {
      const li = document.createElement("li"); li.className = "chip"; li.textContent = t; tone.appendChild(li);
    });
    const palette = document.getElementById("sColors");
    palette.innerHTML = "";
    (sg.colors || []).forEach((c) => {
      const sw = document.createElement("div");
      sw.className = "swatch";
      sw.style.background = c.hex;
      sw.style.color = contrastText(c.hex);
      sw.innerHTML = `<span class="swatch__name">${escape(c.name)}</span>
        <span class="swatch__meta">${escape(c.role || "")} · ${escape(c.hex)}</span>`;
      palette.appendChild(sw);
    });
    const fonts = document.getElementById("sFonts");
    fonts.innerHTML = "";
    (sg.fonts || []).forEach((f) => {
      const el = document.createElement("div");
      el.className = "font";
      const family = (f.google_font || f.name).replace(/\s+/g, "+");
      loadGoogleFont(f.google_font || f.name);
      el.innerHTML = `<span class="font__name" style="font-family:'${escape(f.google_font || f.name)}',sans-serif">${escape(f.name)}</span>
        <span class="muted">${escape(f.role)} · ${escape(f.google_font || f.fallback)}</span>`;
      fonts.appendChild(el);
    });
    document.getElementById("sImagery").textContent = sg.imagery_direction || "";
    fillList("sMessages", sg.key_messages || []);
    fillList("sDo", sg.do_say || []);
    fillList("sDont", sg.dont_say || []);
    const c = document.getElementById("sCampaigns");
    c.innerHTML = "";
    (sg.campaign_ideas || []).forEach((ci) => {
      const el = document.createElement("div");
      el.className = "campaign";
      el.innerHTML = `<div class="campaign__title">${escape(ci.title)}</div>
        <div class="campaign__hook">${escape(ci.hook)}</div>
        <p class="muted" style="margin-top:6px;font-size:12px">${escape(ci.narrative)}</p>
        <span class="campaign__cta">${escape(ci.primary_cta)}</span>`;
      c.appendChild(el);
    });
  }

  document.getElementById("goMockupsBtn").addEventListener("click", async () => {
    markDone("style");
    go("collage");
    const count = parseInt(document.getElementById("mockupCount").value, 10) || 16;
    await runCollageFlow(count);
  });

  // ---------------------------------------------------------------------
  // Step 4: collage
  // ---------------------------------------------------------------------
  async function runCollageFlow(count) {
    document.getElementById("collageLoader").hidden = false;
    document.getElementById("collageView").hidden = true;
    const status = document.getElementById("collageStatus");
    const msgs = [
      "Planning distinct concepts…",
      "Calling OpenAI gpt-image-1…",
      "Rendering mockups in parallel…",
      "Finalizing the collage…",
    ];
    let i = 0;
    const tick = setInterval(() => { status.textContent = msgs[(++i) % msgs.length]; }, 2000);
    try {
      const { mockups } = await api.mockups(state.projectId, { count });
      clearInterval(tick);
      state.mockups = mockups;
      // Snapshot original text for the Reset button in the refine step
      state.originalConcepts = new Map(
        mockups.map((m) => [m.concept.id, {
          headline: m.concept.headline,
          subheadline: m.concept.subheadline || "",
          cta: m.concept.cta,
          body_copy: m.concept.body_copy || "",
          visual_prompt: m.concept.visual_prompt,
        }])
      );
      renderCollage(mockups);
      document.getElementById("collageLoader").hidden = true;
      document.getElementById("collageView").hidden = false;
    } catch (err) {
      clearInterval(tick);
      alert("Mockup generation failed: " + err.message);
    }
  }

  function renderCollage(mockups) {
    const grid = document.getElementById("collageGrid");
    grid.innerHTML = "";
    mockups.forEach((m) => {
      const tile = document.createElement("div");
      tile.className = "tile";
      const platform = (m.concept.platform || "").replace(/_/g, " ");
      tile.innerHTML = `
        <img loading="lazy" src="${m.image_url}" alt="${escape(m.concept.headline)}" />
        <span class="tile__platform">${escape(platform)}</span>
        <div class="tile__overlay">
          <h3 class="tile__headline">${escape(m.concept.headline)}</h3>
          <span class="tile__cta">${escape(m.concept.cta)}</span>
        </div>`;
      tile.addEventListener("click", () => openPreview(m));
      grid.appendChild(tile);
    });
  }

  document.getElementById("regenerateBtn").addEventListener("click", async () => {
    const count = parseInt(document.getElementById("mockupCount").value, 10) || 16;
    await runCollageFlow(count);
  });

  document.getElementById("goVoteBtn").addEventListener("click", () => {
    markDone("collage");
    go("vote");
    renderVoting(state.mockups);
  });

  // ---------------------------------------------------------------------
  // Step 5: voting
  // ---------------------------------------------------------------------
  function renderVoting(mockups) {
    const grid = document.getElementById("votingGrid");
    grid.innerHTML = "";
    mockups.forEach((m) => {
      const id = m.concept.id;
      if (!state.votes.has(id)) state.votes.set(id, { value: 0, heart: false, reject: false });
      const v = state.votes.get(id);
      const card = document.createElement("div");
      card.className = "vote-card";
      card.innerHTML = `
        <div class="vote-card__img"><img src="${m.image_url}" alt=""></div>
        <div class="vote-card__body">
          <div class="vote-card__meta">${escape((m.concept.platform || "").replace(/_/g, " "))} · ${escape(m.concept.campaign || "")}</div>
          <h3 class="vote-card__headline">${escape(m.concept.headline)}</h3>
          <div class="stars" data-id="${id}">
            ${[1,2,3,4,5].map((n) => `<button data-star="${n}" class="${v.value >= n ? 'is-active' : ''}">★</button>`).join("")}
          </div>
        </div>
        <div class="vote-card__actions">
          <button class="vote-btn vote-btn--heart ${v.heart ? 'is-active' : ''}" data-action="heart">♥ Love</button>
          <button class="vote-btn vote-btn--reject ${v.reject ? 'is-active' : ''}" data-action="reject">✕ Pass</button>
          <button class="vote-btn" data-action="preview">⤢ Preview</button>
        </div>`;
      card.querySelectorAll(".stars button").forEach((btn) => {
        btn.addEventListener("click", () => {
          const n = parseInt(btn.dataset.star, 10);
          v.value = v.value === n ? 0 : n;
          v.reject = false;
          renderVoting(mockups);
          updateVoteStatus();
        });
      });
      card.querySelector('[data-action="heart"]').addEventListener("click", () => {
        v.heart = !v.heart;
        if (v.heart && v.value < 4) v.value = 4;
        v.reject = false;
        renderVoting(mockups);
        updateVoteStatus();
      });
      card.querySelector('[data-action="reject"]').addEventListener("click", () => {
        v.reject = !v.reject;
        if (v.reject) { v.value = -1; v.heart = false; }
        else if (v.value < 0) v.value = 0;
        renderVoting(mockups);
        updateVoteStatus();
      });
      card.querySelector('[data-action="preview"]').addEventListener("click", () => openPreview(m));
      grid.appendChild(card);
    });
    updateVoteStatus();
  }

  function updateVoteStatus() {
    let cast = 0, love = 0;
    state.votes.forEach((v) => {
      if (v.value !== 0 || v.heart || v.reject) cast++;
      if (v.heart) love++;
    });
    document.getElementById("voteStatus").textContent = `${cast} voted · ${love} favorites`;
  }

  document.getElementById("goRefineBtn").addEventListener("click", async () => {
    markDone("vote");

    // Flush votes first so the backend score matches the UI ordering.
    const votes = [];
    state.votes.forEach((v, mockup_id) => {
      if (v.heart) votes.push({ mockup_id, value: Math.max(v.value, 4) });
      else if (v.reject) votes.push({ mockup_id, value: -1 });
      else if (v.value > 0) votes.push({ mockup_id, value: v.value });
    });
    try { if (votes.length) await api.vote(state.projectId, { votes }); } catch (_) {}

    // Rank: hearts first, then stars, then fall back to top 4 by positive votes.
    const ranked = [...state.votes.entries()]
      .map(([id, v]) => ({ id, score: (v.heart ? 10 : 0) + (v.value > 0 ? v.value : 0) }))
      .filter((r) => r.score > 0)
      .sort((a, b) => b.score - a.score);
    let picked = ranked.slice(0, 6).map((r) => r.id);
    if (!picked.length) picked = state.mockups.slice(0, 4).map((m) => m.concept.id);

    state.refinePicks = picked;
    go("refine");
    renderRefine();
  });

  document.getElementById("backToVoteBtn").addEventListener("click", () => go("vote"));

  document.getElementById("goBuildBtn").addEventListener("click", async () => {
    markDone("refine");
    go("build");
    await runBuildFlow();
  });

  // ---------------------------------------------------------------------
  // Step 6: refine
  // ---------------------------------------------------------------------
  const saveTimers = new Map();

  function mockupById(id) { return state.mockups.find((m) => m.concept.id === id); }

  function renderRefine() {
    const grid = document.getElementById("refineGrid");
    grid.innerHTML = "";
    const picks = state.refinePicks || [];
    document.getElementById("refineStatus").textContent =
      `${picks.length} concept${picks.length === 1 ? "" : "s"} selected from your votes`;

    const sg = state.styleGuide || {};
    const accent = (sg.colors || []).find((c) => (c.role || "").includes("accent"))?.hex
      || (sg.colors || [])[1]?.hex || "#FF5A1F";
    const display = (sg.fonts || []).find((f) => f.role === "display")?.google_font || "Space Grotesk";
    loadGoogleFont(display);

    picks.forEach((id) => {
      const m = mockupById(id);
      if (!m) return;
      const vote = state.votes.get(id) || {};
      const aspectClass = m.width > m.height * 1.2 ? "is-wide" : (m.height > m.width * 1.2 ? "is-tall" : "");

      const card = document.createElement("div");
      card.className = "refine-card";
      card.dataset.id = id;
      card.innerHTML = `
        <div class="refine-card__preview ${aspectClass}">
          <img class="refine-card__bg" src="${m.image_url}" alt="">
          <div class="refine-card__scrim"></div>
          <div class="refine-card__text">
            <h3 data-bind="headline" style="font-family:'${display}',sans-serif">${escape(m.concept.headline)}</h3>
            <p data-bind="subheadline">${escape(m.concept.subheadline || "")}</p>
          </div>
          <div class="refine-card__cta-pos">
            <span class="refine-card__cta" data-bind="cta" style="background:${accent};color:${contrastText(accent)}">${escape(m.concept.cta)}</span>
          </div>
          <button class="refine-card__regen" data-act="regen" title="Regenerate this image">↻ New image</button>
        </div>
        <div class="refine-card__body">
          <div class="refine-card__badges">
            <span class="chip">${escape((m.concept.platform || "").replace(/_/g, " "))}</span>
            <span class="chip">${escape(m.concept.campaign || "")}</span>
            ${vote.heart ? '<span class="chip vote">♥ Loved</span>' : ''}
            ${vote.value > 0 && !vote.heart ? `<span class="chip vote">${'★'.repeat(vote.value)}</span>` : ''}
          </div>
          <div class="refine-field">
            <label>Headline</label>
            <input data-field="headline" value="${escape(m.concept.headline)}" />
          </div>
          <div class="refine-field">
            <label>Subheadline</label>
            <input data-field="subheadline" value="${escape(m.concept.subheadline || '')}" />
          </div>
          <div class="refine-field">
            <label>Call to action</label>
            <input data-field="cta" value="${escape(m.concept.cta)}" />
          </div>
          <div class="refine-field">
            <label>Caption / body copy</label>
            <textarea data-field="body_copy">${escape(m.concept.body_copy || '')}</textarea>
          </div>
          <div class="refine-field">
            <label>Image prompt (edit then regenerate)</label>
            <textarea data-field="visual_prompt">${escape(m.concept.visual_prompt)}</textarea>
          </div>
          <div class="refine-card__actions">
            <button data-act="reset">Reset text</button>
            <button data-act="remove">Remove from build</button>
          </div>
          <span class="muted" data-role="status"></span>
        </div>`;
      wireRefineCard(card, m);
      grid.appendChild(card);
    });
  }

  function wireRefineCard(card, mockup) {
    const id = mockup.concept.id;
    const statusEl = card.querySelector('[data-role="status"]');
    const previewText = (field) => card.querySelector(`[data-bind="${field}"]`);

    card.querySelectorAll("[data-field]").forEach((el) => {
      el.addEventListener("input", () => {
        const field = el.dataset.field;
        const value = el.value;
        // Live preview
        const bound = previewText(field);
        if (bound) bound.textContent = value || "";
        // Debounced save
        if (saveTimers.has(id + ":" + field)) clearTimeout(saveTimers.get(id + ":" + field));
        const t = setTimeout(async () => {
          statusEl.textContent = "Saving…";
          try {
            const updated = await api.updateMockup(state.projectId, id, { [field]: value });
            mockup.concept = updated.concept;
            statusEl.textContent = "Saved ✓";
            setTimeout(() => { statusEl.textContent = ""; }, 1200);
          } catch (err) {
            statusEl.textContent = "Save failed: " + err.message;
          }
        }, 500);
        saveTimers.set(id + ":" + field, t);
      });
    });

    card.querySelector('[data-act="regen"]').addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.classList.add("is-loading");
      btn.textContent = "↻ Generating…";
      const promptField = card.querySelector('[data-field="visual_prompt"]');
      const newPrompt = promptField ? promptField.value : null;
      try {
        const updated = await api.regenerate(state.projectId, id, newPrompt ? { visual_prompt: newPrompt } : {});
        // Replace local state + bust the image cache
        const idx = state.mockups.findIndex((m) => m.concept.id === id);
        if (idx >= 0) state.mockups[idx] = updated;
        const bg = card.querySelector(".refine-card__bg");
        const bust = `?v=${Date.now()}`;
        bg.src = (updated.image_url || mockup.image_url) + bust;
      } catch (err) {
        alert("Regenerate failed: " + err.message);
      } finally {
        btn.classList.remove("is-loading");
        btn.textContent = "↻ New image";
      }
    });

    card.querySelector('[data-act="reset"]').addEventListener("click", async () => {
      // No server-side "undo" — just restore the original concept we rendered initially if we still have it.
      const original = state.originalConcepts && state.originalConcepts.get(id);
      if (!original) return;
      Object.assign(mockup.concept, original);
      try { await api.updateMockup(state.projectId, id, original); } catch (_) {}
      renderRefine();
    });

    card.querySelector('[data-act="remove"]').addEventListener("click", () => {
      state.refinePicks = (state.refinePicks || []).filter((x) => x !== id);
      renderRefine();
    });
  }

  // ---------------------------------------------------------------------
  // Step 7: build
  // ---------------------------------------------------------------------
  async function runBuildFlow() {
    document.getElementById("buildLoader").hidden = false;
    document.getElementById("buildView").hidden = true;
    try {
      const picks = state.refinePicks && state.refinePicks.length
        ? { mockup_ids: state.refinePicks }
        : {};
      const result = await api.build(state.projectId, picks);
      renderBuilt(result);
      markDone("build");
      document.getElementById("buildLoader").hidden = true;
      document.getElementById("buildView").hidden = false;
    } catch (err) {
      alert("Build failed: " + err.message);
    }
  }

  function renderBuilt(result) {
    const grid = document.getElementById("builtGrid");
    grid.innerHTML = "";
    const byId = new Map(state.mockups.map((m) => [m.concept.id, m]));
    (result.built || []).forEach((b) => {
      const m = byId.get(b.mockup_id);
      const card = document.createElement("div");
      card.className = "built-card";
      card.innerHTML = `
        <div class="built-card__img"><img src="${m ? m.image_url : ''}" alt=""></div>
        <div class="built-card__body">
          <div class="built-card__title">${m ? escape(m.concept.headline) : 'Asset'}</div>
          <div class="muted">${m ? escape((m.concept.platform || '').replace(/_/g, ' ')) : ''}</div>
          <div class="built-card__row">
            <a class="primary" href="${b.download_url}" download>⬇ ZIP</a>
          </div>
        </div>`;
      grid.appendChild(card);
    });
    document.getElementById("downloadAll").href = result.all_download_url || "#";
  }

  document.getElementById("startOverBtn").addEventListener("click", () => {
    location.reload();
  });

  // ---------------------------------------------------------------------
  // Preview modal
  // ---------------------------------------------------------------------
  const modal = document.getElementById("previewModal");
  modal.addEventListener("click", (e) => { if (e.target.dataset.close !== undefined) modal.hidden = true; });
  function openPreview(m) {
    const body = document.getElementById("previewBody");
    const sg = state.styleGuide || {};
    const accent = (sg.colors || []).find((c) => (c.role || "").includes("accent"))?.hex
      || (sg.colors || [])[1]?.hex || "#FF5A1F";
    const display = (sg.fonts || []).find((f) => f.role === "display")?.google_font || "Space Grotesk";
    loadGoogleFont(display);
    body.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start">
        <div style="position:relative;border-radius:12px;overflow:hidden;aspect-ratio:${m.width}/${m.height};background:#111">
          <img src="${m.image_url}" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">
          <div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,0) 40%,rgba(0,0,0,.7))"></div>
          <div style="position:absolute;left:6%;right:6%;top:8%;color:#fff">
            <h3 style="font-family:'${display}',sans-serif;font-weight:800;font-size:clamp(22px,3vw,38px);line-height:1.05;margin:0">${escape(m.concept.headline)}</h3>
            ${m.concept.subheadline ? `<p style="opacity:.9;margin-top:8px">${escape(m.concept.subheadline)}</p>` : ''}
          </div>
          <div style="position:absolute;left:6%;bottom:7%">
            <span style="display:inline-block;padding:10px 16px;border-radius:999px;background:${accent};color:#fff;font-weight:600;font-size:13px">${escape(m.concept.cta)}</span>
          </div>
        </div>
        <div>
          <div class="muted" style="text-transform:uppercase;letter-spacing:.08em;font-size:11px">${escape((m.concept.platform || '').replace(/_/g,' '))} · ${escape(m.concept.campaign || '')}</div>
          <h2 style="margin:6px 0 10px">${escape(m.concept.headline)}</h2>
          ${m.concept.subheadline ? `<p>${escape(m.concept.subheadline)}</p>` : ''}
          ${m.concept.body_copy ? `<p class="muted">${escape(m.concept.body_copy)}</p>` : ''}
          <h3 style="margin-top:14px">Image prompt</h3>
          <p class="muted" style="font-family:ui-monospace,Menlo,monospace;font-size:12px">${escape(m.concept.visual_prompt)}</p>
          <div class="palette" style="margin-top:10px">
            ${(m.concept.palette || []).map((hx) => `<div class="swatch" style="background:${hx};color:${contrastText(hx)};aspect-ratio:3/1"><span class="swatch__meta">${escape(hx)}</span></div>`).join('')}
          </div>
        </div>
      </div>`;
    modal.hidden = false;
  }

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------
  async function fetchJSON(url, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const res = await fetch(url, init);
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = j.detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return res.json();
  }

  function fillList(id, items) {
    const el = document.getElementById(id);
    el.innerHTML = "";
    items.forEach((t) => {
      const li = document.createElement("li");
      li.textContent = t;
      el.appendChild(li);
    });
  }

  function escape(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function contrastText(hex) {
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return "#fff";
    const r = parseInt(h.slice(0,2), 16);
    const g = parseInt(h.slice(2,4), 16);
    const b = parseInt(h.slice(4,6), 16);
    const lum = (0.299*r + 0.587*g + 0.114*b) / 255;
    return lum > 0.6 ? "#0E1116" : "#FFFFFF";
  }

  const loadedFonts = new Set();
  function loadGoogleFont(family) {
    if (!family || loadedFonts.has(family)) return;
    loadedFonts.add(family);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `https://fonts.googleapis.com/css2?family=${family.replace(/\s+/g, "+")}:wght@400;600;700;800&display=swap`;
    document.head.appendChild(link);
  }

  function setButtonBusy(btn, busy, label) {
    if (!btn) return;
    btn.disabled = busy;
    if (label) btn.textContent = label;
  }

  // Kickoff
  go("brief");
})();
