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
    kit:        (id, mid, body)   => fetchJSON(`/api/social/projects/${id}/mockups/${mid}/kit`, { method: "POST", body: body || {} }),
    uploadLogo: async (id, file) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`/api/social/projects/${id}/logo`, { method: "POST", body: fd });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      return res.json();
    },
    build:      (id, body) => fetchJSON(`/api/social/projects/${id}/build`, { method: "POST", body }),
    health:     ()        => fetchJSON("/api/social/health"),
    listProjects: ()      => fetchJSON("/api/social/projects"),
    deleteProject: (id)   => fetchJSON(`/api/social/projects/${id}`, { method: "DELETE" }),
    getProject:   (id)    => fetchJSON(`/api/social/projects/${id}`),
  };

  // ---------------------------------------------------------------------
  // Toast notifications (replaces alert)
  // ---------------------------------------------------------------------
  const toastsEl = document.getElementById("toasts");
  function toast(message, kind = "info", timeout = 4000) {
    const el = document.createElement("div");
    el.className = `toast toast--${kind}`;
    el.innerHTML = `<span>${escape(message)}</span><button aria-label="Dismiss">✕</button>`;
    const dismiss = () => {
      el.classList.add("is-leaving");
      setTimeout(() => el.remove(), 200);
    };
    el.querySelector("button").addEventListener("click", dismiss);
    toastsEl.appendChild(el);
    if (timeout) setTimeout(dismiss, timeout);
    return dismiss;
  }
  // Surface unhandled promise errors as toasts (safety net)
  window.addEventListener("unhandledrejection", (e) => {
    const msg = (e.reason && e.reason.message) || String(e.reason);
    if (msg) toast(msg, "err");
  });

  const state = {
    projectId: null,
    research: null,
    styleGuide: null,
    mockups: [],
    votes: new Map(), // mockup_id -> { value, heart, reject }
    refinePicks: [],
    originalConcepts: new Map(), // mockup_id -> snapshot of text fields
    logoUrl: null,
    pendingLogoFile: null, // holds logo selected before a project exists
    voteFocusIdx: 0,
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
  // Logo picker (runs before project is created - file is held and uploaded after)
  const logoInput = document.getElementById("logoInput");
  const logoPreview = document.getElementById("logoPreview");
  const logoClear = document.getElementById("logoClear");
  logoInput.addEventListener("change", () => {
    const file = logoInput.files && logoInput.files[0];
    if (!file) return;
    state.pendingLogoFile = file;
    const url = URL.createObjectURL(file);
    logoPreview.innerHTML = `<img src="${url}" alt="">`;
    logoPreview.classList.add("has-image");
    logoClear.hidden = false;
  });
  logoClear.addEventListener("click", () => {
    state.pendingLogoFile = null;
    logoInput.value = "";
    logoPreview.innerHTML = "";
    logoPreview.classList.remove("has-image");
    logoClear.hidden = true;
    state.logoUrl = null;
  });

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
      // Upload logo if one was selected
      if (state.pendingLogoFile) {
        try {
          const logo = await api.uploadLogo(state.projectId, state.pendingLogoFile);
          state.logoUrl = logo.url;
        } catch (err) {
          console.warn("logo upload failed", err);
        }
      }
      markDone("brief");
      go("research");
      await runResearchFlow();
    } catch (err) {
      toast("Couldn’t start: " + err.message, "err");
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
      toast("Research failed: " + err.message, "err");
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
      toast("Style guide failed: " + err.message, "err");
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
  let collageSse = null;
  async function runCollageFlow(count) {
    if (collageSse) collageSse.close();
    state.mockups = [];
    const grid = document.getElementById("collageGrid");
    grid.innerHTML = "";
    const progressEl = document.getElementById("collageProgress");
    const fillEl = document.getElementById("collageProgressFill");
    const labelEl = document.getElementById("collageProgressLabel");
    const headlineEl = document.getElementById("collageHeadline");
    const footEl = document.getElementById("collageFoot");
    progressEl.hidden = false;
    footEl.hidden = true;
    fillEl.style.width = "0%";
    labelEl.textContent = "Planning…";
    headlineEl.textContent = "Tiles appear live as each one finishes — concurrent with retries on failure.";

    const platforms = [...document.querySelectorAll("#platformChips input:checked")].map((i) => i.value);
    const url = `/api/social/projects/${state.projectId}/mockups/stream`
      + `?count=${count}&platforms=${platforms.join(",")}`;

    return new Promise((resolve) => {
      const sse = new EventSource(url);
      collageSse = sse;
      let total = count;
      let completed = 0;
      // Render placeholder tiles up front so users see structure immediately.
      const renderPlaceholders = (n) => {
        grid.innerHTML = "";
        for (let i = 0; i < n; i++) {
          const t = document.createElement("div");
          t.className = "tile is-pending";
          t.dataset.idx = i;
          grid.appendChild(t);
        }
      };

      sse.onmessage = (ev) => {
        let data;
        try { data = JSON.parse(ev.data); } catch (_) { return; }
        if (data.type === "status") {
          headlineEl.textContent = data.status === "planning"
            ? "Planning distinct concepts…"
            : headlineEl.textContent;
        } else if (data.type === "plan") {
          total = data.total;
          renderPlaceholders(total);
          labelEl.textContent = `0 / ${total}`;
        } else if (data.type === "tile") {
          const m = data.asset;
          state.mockups.push(m);
          state.originalConcepts.set(m.concept.id, {
            headline: m.concept.headline,
            subheadline: m.concept.subheadline || "",
            cta: m.concept.cta,
            body_copy: m.concept.body_copy || "",
            visual_prompt: m.concept.visual_prompt,
          });
          // Replace the next pending placeholder with the real tile.
          const slot = grid.querySelector(".tile.is-pending");
          if (slot) {
            slot.replaceWith(buildTile(m));
          } else {
            grid.appendChild(buildTile(m));
          }
          completed = data.completed;
          fillEl.style.width = `${(completed / total) * 100}%`;
          labelEl.textContent = `${completed} / ${total}`;
        } else if (data.type === "done") {
          fillEl.style.width = "100%";
          labelEl.textContent = `${total} / ${total} ✓`;
          headlineEl.textContent = "Done — pick the ones you love.";
          footEl.hidden = false;
          toast(`Generated ${total} mockups`, "ok", 2500);
          sse.close();
          collageSse = null;
          resolve();
        } else if (data.type === "error") {
          toast("Mockup generation failed: " + data.detail, "err");
          sse.close();
          collageSse = null;
          resolve();
        }
      };
      sse.onerror = () => {
        if (sse.readyState === EventSource.CLOSED) return;
        toast("Lost streaming connection. Reload to retry.", "err");
        sse.close();
        collageSse = null;
        resolve();
      };
    });
  }

  function buildTile(m) {
    const tile = document.createElement("div");
    tile.className = "tile";
    tile.dataset.id = m.concept.id;
    const platform = (m.concept.platform || "").replace(/_/g, " ");
    tile.innerHTML = `
      <img loading="lazy" src="${m.image_url}" alt="${escape(m.concept.headline)}" />
      <span class="tile__platform">${escape(platform)}</span>
      <button class="tile__retry" data-act="retry" title="Regenerate this tile">↻</button>
      <div class="tile__overlay">
        <h3 class="tile__headline">${escape(m.concept.headline)}</h3>
        <span class="tile__cta">${escape(m.concept.cta)}</span>
      </div>`;
    tile.addEventListener("click", (e) => {
      if (e.target.closest("[data-act='retry']")) return;
      openPreview(m);
    });
    tile.querySelector('[data-act="retry"]').addEventListener("click", async (e) => {
      e.stopPropagation();
      const btn = e.currentTarget;
      btn.textContent = "…";
      try {
        const updated = await api.regenerate(state.projectId, m.concept.id, {});
        const idx = state.mockups.findIndex((x) => x.concept.id === m.concept.id);
        if (idx >= 0) state.mockups[idx] = updated;
        const fresh = buildTile(updated);
        tile.replaceWith(fresh);
        toast("Tile regenerated", "ok", 2000);
      } catch (err) {
        toast("Retry failed: " + err.message, "err");
        btn.textContent = "↻";
      }
    });
    return tile;
  }

  function renderCollage(mockups) {
    const grid = document.getElementById("collageGrid");
    grid.innerHTML = "";
    mockups.forEach((m) => grid.appendChild(buildTile(m)));
    document.getElementById("collageFoot").hidden = false;
  }

  document.getElementById("regenerateBtn").addEventListener("click", async () => {
    const count = parseInt(document.getElementById("mockupCount").value, 10) || 16;
    await runCollageFlow(count);
  });

  document.getElementById("goVoteBtn").addEventListener("click", () => {
    markDone("collage");
    go("vote");
    renderVoting(state.mockups);
    enableVoteShortcuts();
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
      card.dataset.id = id;
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

  let voteKeyHandler = null;
  function enableVoteShortcuts() {
    if (voteKeyHandler) return;
    voteKeyHandler = (e) => {
      // Only when on the vote step and not typing in an input
      const inVote = !document.querySelector('.step[data-step="vote"]').hidden;
      if (!inVote) return;
      const tag = (e.target && e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || e.metaKey || e.ctrlKey) return;

      const cards = [...document.querySelectorAll("#votingGrid .vote-card")];
      if (!cards.length) return;
      // Clamp focus index
      state.voteFocusIdx = Math.min(Math.max(state.voteFocusIdx, 0), cards.length - 1);
      const focus = cards[state.voteFocusIdx];
      const id = focus.dataset.id;
      const v = state.votes.get(id) || { value: 0, heart: false, reject: false };
      let handled = true;
      switch (e.key.toLowerCase()) {
        case "j":
        case "arrowright":
          state.voteFocusIdx = Math.min(state.voteFocusIdx + 1, cards.length - 1); break;
        case "k":
        case "arrowleft":
          state.voteFocusIdx = Math.max(state.voteFocusIdx - 1, 0); break;
        case "l":
          v.heart = !v.heart; if (v.heart) { v.value = Math.max(v.value, 4); v.reject = false; }
          state.votes.set(id, v); break;
        case "x":
          v.reject = !v.reject; if (v.reject) { v.value = -1; v.heart = false; }
          else if (v.value < 0) v.value = 0;
          state.votes.set(id, v); break;
        case "1": case "2": case "3": case "4": case "5":
          const n = parseInt(e.key, 10);
          v.value = v.value === n ? 0 : n; v.reject = false;
          state.votes.set(id, v); break;
        default: handled = false;
      }
      if (handled) {
        e.preventDefault();
        renderVoting(state.mockups);
        // Re-find focused card and scroll into view
        const redrawn = document.querySelectorAll("#votingGrid .vote-card");
        redrawn.forEach((c, i) => c.classList.toggle("is-focus", i === state.voteFocusIdx));
        if (redrawn[state.voteFocusIdx]) {
          redrawn[state.voteFocusIdx].scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      }
    };
    window.addEventListener("keydown", voteKeyHandler);
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

      const variants = m.concept.variants || [];
      const variantChips = variants.map((v, vi) =>
        `<button class="variant-chip" data-variant="${vi}" title="${escape(v.headline)}">Variant ${vi + 2}</button>`
      ).join("");
      const logoHtml = state.logoUrl
        ? `<img class="refine-card__logo" src="${state.logoUrl}?v=${Date.now()}" alt="">`
        : "";
      const hashtags = (m.concept.hashtags || []).map((h) => `#${h.replace(/^#/, '')}`).join(" ");

      const card = document.createElement("div");
      card.className = "refine-card";
      card.dataset.id = id;
      card.innerHTML = `
        <div class="refine-card__preview ${aspectClass}">
          <img class="refine-card__bg" src="${m.image_url}" alt="">
          <div class="refine-card__scrim"></div>
          ${logoHtml}
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
          ${variants.length ? `
          <div class="refine-field">
            <label>Copy variants (A/B)</label>
            <div class="variants">
              <button class="variant-chip is-active" data-variant="primary">Primary</button>
              ${variantChips}
            </div>
          </div>` : ''}
          <div class="caption-block">
            <label style="display:flex;align-items:center;justify-content:space-between">
              Caption
              <span style="display:inline-flex;gap:6px">
                <button type="button" class="copy-btn" data-copy="caption">📋 Copy</button>
                ${hashtags ? `<button type="button" class="copy-btn" data-copy="hashtags">#</button>` : ''}
                ${m.concept.alt_text ? `<button type="button" class="copy-btn" data-copy="alt">alt</button>` : ''}
              </span>
            </label>
            <textarea data-field="caption">${escape(m.concept.caption || m.concept.body_copy || '')}</textarea>
            ${hashtags ? `<div class="hashtags">${escape(hashtags)}</div>` : ''}
            ${m.concept.alt_text ? `<div class="alt"><strong>Alt:</strong> ${escape(m.concept.alt_text)}</div>` : ''}
          </div>
          <div class="refine-field">
            <label>Image prompt (edit then regenerate)</label>
            <textarea data-field="visual_prompt">${escape(m.concept.visual_prompt)}</textarea>
          </div>
          <div class="kit-strip">
            <div class="kit-strip__header">
              <h4>Campaign kit — multi-format</h4>
              <button class="primary" data-act="kit">+ Adapt to all platforms</button>
            </div>
            <div class="kit-strip__row" data-role="kit-row">
              ${(m.kit || []).map((k) => kitTileHtml(k)).join("")}
            </div>
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

  function kitTileHtml(asset) {
    const label = (asset.concept.platform || "").replace(/_/g, " ");
    return `<div class="kit-tile" title="${escape(label)}">
      <img src="${asset.image_url}" alt="">
      <span class="kit-tile__label">${escape(label)}</span>
    </div>`;
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

    // Variant swapping
    card.querySelectorAll(".variant-chip").forEach((chip) => {
      chip.addEventListener("click", async () => {
        card.querySelectorAll(".variant-chip").forEach((c) => c.classList.remove("is-active"));
        chip.classList.add("is-active");
        const key = chip.dataset.variant;
        let next;
        if (key === "primary") {
          const orig = state.originalConcepts.get(id);
          if (!orig) return;
          next = { headline: orig.headline, subheadline: orig.subheadline, cta: orig.cta };
        } else {
          const v = (mockup.concept.variants || [])[parseInt(key, 10)];
          if (!v) return;
          next = { headline: v.headline, subheadline: v.subheadline || "", cta: v.cta };
        }
        // Update inputs + overlay
        const setField = (name, val) => {
          const inp = card.querySelector(`[data-field="${name}"]`);
          if (inp) inp.value = val || "";
          const bound = card.querySelector(`[data-bind="${name}"]`);
          if (bound) bound.textContent = val || "";
          mockup.concept[name] = val;
        };
        setField("headline", next.headline);
        setField("subheadline", next.subheadline);
        setField("cta", next.cta);
        try { await api.updateMockup(state.projectId, id, next); } catch (_) {}
      });
    });

    // Campaign Kit button
    const kitBtn = card.querySelector('[data-act="kit"]');
    if (kitBtn) kitBtn.addEventListener("click", async () => {
      kitBtn.disabled = true;
      const originalLabel = kitBtn.textContent;
      kitBtn.textContent = "Adapting…";
      try {
        const { kit } = await api.kit(state.projectId, id, {});
        mockup.kit = kit;
        const row = card.querySelector('[data-role="kit-row"]');
        row.innerHTML = kit.map((k) => kitTileHtml(k)).join("");
      } catch (err) {
        toast("Kit adaptation failed: " + err.message, "err");
      } finally {
        kitBtn.disabled = false;
        kitBtn.textContent = originalLabel;
      }
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
        toast("Regenerate failed: " + err.message, "err");
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
      toast("Build failed: " + err.message, "err");
    }
  }

  function renderBuilt(result) {
    const grid = document.getElementById("builtGrid");
    grid.innerHTML = "";
    // Index every mockup + every kit sibling by id
    const byId = new Map();
    state.mockups.forEach((m) => {
      byId.set(m.concept.id, m);
      (m.kit || []).forEach((k) => byId.set(k.concept.id, k));
    });
    (result.built || []).forEach((b) => {
      const m = byId.get(b.mockup_id);
      const tags = m && m.concept.hashtags && m.concept.hashtags.length
        ? `<div class="muted" style="font-size:11px;color:#7ab7ff">${m.concept.hashtags.map((h) => '#' + h.replace(/^#/, '')).join(' ')}</div>`
        : "";
      const card = document.createElement("div");
      card.className = "built-card";
      card.innerHTML = `
        <div class="built-card__img"><img src="${m ? m.image_url : ''}" alt=""></div>
        <div class="built-card__body">
          <div class="built-card__title">${m ? escape(m.concept.headline) : 'Asset'}</div>
          <div class="muted">${m ? escape((m.concept.platform || '').replace(/_/g, ' ')) : ''}${m && m.kit_parent_id ? ' · kit' : ''}</div>
          ${tags}
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

  // ---------------------------------------------------------------------
  // Capabilities probe
  // ---------------------------------------------------------------------
  async function refreshCapabilities() {
    try {
      const c = await api.health();
      const set = (sel, on, label) => {
        const el = document.querySelector(`#capabilities .cap[data-cap="${sel}"]`);
        if (!el) return;
        el.classList.toggle("is-on", on);
        el.classList.toggle("is-off", !on);
        el.title = `${label}: ${on ? "ready" : "not configured"}`;
      };
      set("gemini", !!c.gemini_text, "Gemini");
      set("openai", !!c.openai_image, "OpenAI gpt-image-1");
      if (!c.openai_image && !window.__warnedOpenAI) {
        window.__warnedOpenAI = true;
        toast("OpenAI not configured — using local placeholder images. Add OPENAI_API_KEY for real renders.", "info", 7000);
      }
    } catch (_) { /* ignore */ }
  }

  // ---------------------------------------------------------------------
  // Recent projects
  // ---------------------------------------------------------------------
  async function refreshRecent() {
    let list = [];
    try { ({ projects: list } = await api.listProjects()); } catch (_) { return; }
    const ul = document.getElementById("recentList");
    ul.innerHTML = "";
    list.forEach((p) => {
      const li = document.createElement("li");
      li.dataset.id = p.id;
      const when = relativeTime(new Date(p.updated_at));
      li.innerHTML = `
        <div style="overflow:hidden">
          <div style="font-weight:600; white-space:nowrap; text-overflow:ellipsis; overflow:hidden">${escape(p.company || 'Untitled')}</div>
          <div class="recent-meta">${escape(p.status)} · ${when}</div>
        </div>
        <button class="recent-del" title="Delete">✕</button>`;
      li.addEventListener("click", (e) => {
        if (e.target.closest(".recent-del")) return;
        resumeProject(p.id);
      });
      li.querySelector(".recent-del").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete project "${p.company}"? This removes its assets too.`)) return;
        try {
          await api.deleteProject(p.id);
          toast("Project deleted", "ok", 2000);
          refreshRecent();
        } catch (err) { toast(err.message, "err"); }
      });
      ul.appendChild(li);
    });
  }

  document.getElementById("newProjectBtn").addEventListener("click", () => {
    if (state.projectId && !confirm("Start a new project? Your current project stays saved on the server.")) return;
    location.reload();
  });

  function relativeTime(d) {
    const s = (Date.now() - d.getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  // ---------------------------------------------------------------------
  // Resume a project from disk
  // ---------------------------------------------------------------------
  async function resumeProject(id) {
    try {
      const p = await api.getProject(id);
      state.projectId = p.id;
      state.research = p.research || null;
      state.styleGuide = p.style_guide || null;
      state.mockups = p.mockups || [];
      state.originalConcepts = new Map(
        state.mockups.map((m) => [m.concept.id, {
          headline: m.concept.headline,
          subheadline: m.concept.subheadline || "",
          cta: m.concept.cta,
          body_copy: m.concept.body_copy || "",
          visual_prompt: m.concept.visual_prompt,
        }])
      );
      state.logoUrl = p.logo ? `${p.logo.url}?v=${Date.now()}` : null;
      state.votes = new Map();
      state.refinePicks = [];

      // Restore form/preview if logo present.
      if (state.logoUrl) {
        document.getElementById("logoPreview").innerHTML = `<img src="${state.logoUrl}" alt="">`;
        document.getElementById("logoPreview").classList.add("has-image");
        document.getElementById("logoClear").hidden = false;
      }
      if (state.research) renderResearch(state.research);
      if (state.styleGuide) renderStyleGuide(state.styleGuide);
      if (state.mockups.length) renderCollage(state.mockups);

      // Mark earlier steps done and jump to the right one.
      ["brief", "research", "style", "collage", "vote", "refine"].forEach((s) => {});
      const target = pickResumeStep(p);
      ["brief", "research", "style", "collage", "vote", "refine", "build"].forEach((s) => {
        if (s === target) return;
        // anything earlier than target counts as done
        if (STEPS.indexOf(s) < STEPS.indexOf(target)) doneSteps.add(s);
      });
      // unhide research/style/collage views as appropriate
      if (state.research) {
        document.getElementById("researchLoader").hidden = true;
        document.getElementById("researchReport").hidden = false;
      }
      if (state.styleGuide) {
        document.getElementById("styleLoader").hidden = true;
        document.getElementById("styleView").hidden = false;
      }
      go(target);
      toast(`Resumed "${p.brief.company_name}"`, "ok", 2500);
    } catch (err) {
      toast("Could not resume: " + err.message, "err");
    }
  }

  function pickResumeStep(p) {
    if ((p.built_assets || []).length) return "build";
    if ((p.mockups || []).length) return "vote";
    if (p.style_guide) return "collage";
    if (p.research) return "style";
    return "research";
  }

  // ---------------------------------------------------------------------
  // Keyboard help overlay
  // ---------------------------------------------------------------------
  const kbdHelp = document.getElementById("kbdHelp");
  document.getElementById("helpBtn").addEventListener("click", () => { kbdHelp.hidden = false; });
  kbdHelp.addEventListener("click", (e) => { if (e.target.dataset.close !== undefined) kbdHelp.hidden = true; });
  window.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || e.metaKey || e.ctrlKey) return;
    if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
      e.preventDefault();
      kbdHelp.hidden = !kbdHelp.hidden;
    } else if (e.key === "Escape") {
      kbdHelp.hidden = true;
      document.getElementById("previewModal").hidden = true;
    }
  });

  // ---------------------------------------------------------------------
  // Copy-to-clipboard delegation (for any [data-copy] target)
  // ---------------------------------------------------------------------
  document.body.addEventListener("click", async (e) => {
    const btn = e.target.closest(".copy-btn");
    if (!btn) return;
    const target = btn.getAttribute("data-copy");
    let text = "";
    if (target === "caption") {
      const card = btn.closest(".refine-card");
      text = card?.querySelector('[data-field="caption"]')?.value || "";
    } else if (target === "hashtags") {
      const card = btn.closest(".refine-card");
      text = card?.querySelector('.hashtags')?.textContent || "";
    } else if (target === "alt") {
      const card = btn.closest(".refine-card");
      text = card?.querySelector('.alt')?.textContent.replace(/^Alt:\s*/, "") || "";
    } else if (btn.dataset.text) {
      text = btn.dataset.text;
    }
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      btn.classList.add("is-success");
      const original = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = original; btn.classList.remove("is-success"); }, 1200);
    } catch (_) {
      toast("Couldn’t copy to clipboard", "err");
    }
  });

  // Kickoff
  go("brief");
  refreshCapabilities();
  refreshRecent();
})();
