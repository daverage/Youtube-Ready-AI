(() => {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const fileName = document.getElementById("file-name");
  const startBtn = document.getElementById("start-btn");
  const aiToggle = document.getElementById("ai");
  const modelField = document.getElementById("model-field");
  const backendField = document.getElementById("backend-field");
  const backendSelect = document.getElementById("metadata-backend");
  const modelSelect = document.getElementById("metadata-model-select");
  const modelHint = document.getElementById("model-hint");
  const thumbnailStyleSelect = document.getElementById("thumbnail-style-intent");
  const aiRuntimeStatus = document.getElementById("ai-runtime-status");
  const aiStartBtn = document.getElementById("ai-start-btn");

  let modelsRequestToken = 0;
  let modelsAvailable = false;

  // Local caches often hold non-chat models too (Whisper transcription models,
  // embedding/TTS models); never auto-select one of those as the metadata model.
  const NON_CHAT_MODEL_RE = /whisper|parakeet|clap|clip|embed|-vae\b|tts\b/i;
  const PREFERRED_MODEL_RE = /instruct|-it(?:[-_]|$)|chat/i;

  function pickDefaultModel(models) {
    const usable = models.filter((name) => !NON_CHAT_MODEL_RE.test(name));
    const pool = usable.length ? usable : models;
    return pool.find((name) => PREFERRED_MODEL_RE.test(name)) || pool[0];
  }

  async function refreshModels() {
    const backend = backendSelect.value;
    const token = ++modelsRequestToken;
    modelSelect.innerHTML = '<option value="">Loading models…</option>';
    modelHint.textContent = "";
    aiRuntimeStatus.textContent = "";
    aiStartBtn.hidden = true;

    let models = [];
    let error = null;
    let runtime = null;
    try {
      const response = await fetch(`/api/models?${new URLSearchParams({ backend })}`);
      const data = await response.json();
      models = data.models || [];
      error = data.error || null;
      runtime = data.runtime || null;
    } catch (err) {
      error = err.message || String(err);
    }
    if (token !== modelsRequestToken) return; // a newer request superseded this one

    modelSelect.innerHTML = "";
    modelSelect.removeAttribute("title");
    modelsAvailable = models.length > 0;
    if (models.length) {
      for (const name of models) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        option.title = name; // full name on hover, since the closed select truncates long ones
        modelSelect.appendChild(option);
      }
      modelSelect.value = pickDefaultModel(models);
      modelSelect.title = modelSelect.value;
      modelHint.textContent = `${models.length} found`;
      aiRuntimeStatus.textContent = runtime?.message || "AI runtime ready.";
    } else {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent =
        backend === "mlx"
          ? "No models cached — will use a heuristic fallback instead"
          : error
            ? "Ollama isn't running — will use a heuristic fallback instead"
            : "No models pulled yet — will use a heuristic fallback instead";
      modelSelect.appendChild(empty);
      modelHint.textContent = error ? "not running, will fall back" : "none found, will fall back";
      aiRuntimeStatus.textContent = runtime?.message || error || "AI runtime is not ready.";
      aiStartBtn.hidden = false;
    }
  }

  modelSelect.addEventListener("change", () => {
    modelSelect.title = modelSelect.value;
  });

  aiStartBtn.addEventListener("click", async () => {
    aiStartBtn.disabled = true;
    aiRuntimeStatus.textContent = "Starting AI runtime…";
    try {
      const response = await fetch("/api/ai/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backend: backendSelect.value, model: modelSelect.value || null }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      aiRuntimeStatus.textContent = data.message || "AI runtime start requested.";
      await refreshModels();
    } catch (err) {
      aiRuntimeStatus.textContent = `Could not start AI (${err.message || err}).`;
    } finally {
      aiStartBtn.disabled = false;
    }
  });

  const uploadPanel = document.getElementById("upload-panel");
  const sessionsPanel = document.getElementById("sessions-panel");
  const sessionList = document.getElementById("session-list");
  const progressPanel = document.getElementById("progress-panel");
  const resultsPanel = document.getElementById("results-panel");
  const errorPanel = document.getElementById("error-panel");

  const log = document.getElementById("log");
  const progressTitle = document.getElementById("progress-title");
  const resetBtn = document.getElementById("reset-btn");

  const tabsEl = document.getElementById("tabs");
  const tabsSecondaryEl = document.getElementById("tabs-secondary");
  const viewer = document.getElementById("viewer");
  const copyBtn = document.getElementById("copy-btn");
  const downloadBtn = document.getElementById("download-btn");
  const resultsPath = document.getElementById("results-path");
  const tabToolbar = document.querySelector(".tab-toolbar");
  const workflowSteps = document.getElementById("workflow-steps");
  const filesDisclosure = document.getElementById("files-disclosure");
  const packageChecklist = document.getElementById("package-checklist");
  const packagePanelCount = document.getElementById("package-panel-count");
  const reviewPackageBtn = document.getElementById("review-package-btn");
  const openFolderBtn = document.getElementById("open-folder-btn");

  const thumbnailsToggle = document.getElementById("thumbnails");
  const thumbnailStudio = document.getElementById("thumbnail-studio");
  const studioSwitcher = document.getElementById("studio-switcher");
  const studioEditor = document.getElementById("studio-editor");
  const studioProvenance = document.getElementById("studio-provenance");
  const studioDownloadAll = document.getElementById("studio-download-all");
  const studioActivity = document.getElementById("studio-activity");
  const studioActivityText = document.getElementById("studio-activity-text");
  const studioActivityTime = document.getElementById("studio-activity-time");

  const previewEditor = document.getElementById("preview-editor");
  const previewVideo = document.getElementById("preview-video");
  const cueList = document.getElementById("cue-list");
  const saveTranscriptBtn = document.getElementById("save-transcript-btn");
  const applyDictBtn = document.getElementById("apply-dict-btn");
  const editorStatus = document.getElementById("editor-status");
  const dictionaryForm = document.getElementById("dictionary-form");
  const dictWrong = document.getElementById("dict-wrong");
  const dictRight = document.getElementById("dict-right");
  const dictionaryList = document.getElementById("dictionary-list");
  const dictionaryOffer = document.getElementById("dictionary-offer");
  const dictionaryOfferText = document.getElementById("dictionary-offer-text");
  const dictionaryOfferYes = document.getElementById("dictionary-offer-yes");
  const dictionaryOfferNo = document.getElementById("dictionary-offer-no");

  const errorMessage = document.getElementById("error-message");

  let selectedFile = null;
  let currentJobId = null;
  let currentTab = null;

  const PREVIEWABLE = new Set([
    "transcript.txt",
    "captions.srt",
    "captions.vtt",
    "chapters.txt",
    "description.txt",
    "titles.txt",
    "hashtags.txt",
    "youtube_metadata.json",
    "youtube_package.md",
  ]);

  // Shown as their own gallery tab instead of a raw text/JSON preview.
  const HIDDEN_FROM_TABS = new Set([
    "thumbnail_ideas.txt",
    "thumbnail_ideas.json",
    "thumbnail_variants.json",
  ]);
  const THUMBNAILS_TAB = "\u{1F5BC} Thumbnail Studio";

  function showPanel(panel) {
    for (const el of [uploadPanel, progressPanel, resultsPanel, errorPanel]) {
      el.hidden = el !== panel;
    }
    // The session list lives alongside the upload form, not as its own exclusive panel.
    sessionsPanel.hidden = panel !== uploadPanel;
    if (panel === uploadPanel) refreshSessions();
  }

  function resetToUpload() {
    selectedFile = null;
    currentJobId = null;
    previewLoaded = false;
    previewVideo.pause();
    previewVideo.removeAttribute("src");
    previewVideo.querySelectorAll("track").forEach((el) => el.remove());
    previewVideo.load();
    fileName.textContent = "MP4, MOV, MKV, WAV — stays on this machine";
    startBtn.disabled = true;
    log.innerHTML = "";
    showPanel(uploadPanel);
  }

  function activateWorkflowStep(li) {
    const step = li.dataset.step;
    if (step === "review") selectTab(PREVIEW_TAB);
    else if (step === "thumbnail") selectTab(THUMBNAILS_TAB);
    else if (step === "package") {
      setWorkflowStep("package");
      document.getElementById("package-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (step === "export") {
      setWorkflowStep("export");
      filesDisclosure.open = true;
      filesDisclosure.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  if (workflowSteps) {
    workflowSteps.addEventListener("click", (event) => {
      const li = event.target.closest(".workflow-step");
      if (li) activateWorkflowStep(li);
    });
    // These are <li role="button"> elements, not native <button>s, so Enter/Space
    // activation has to be wired up by hand for keyboard users.
    workflowSteps.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const li = event.target.closest(".workflow-step");
      if (!li) return;
      event.preventDefault();
      activateWorkflowStep(li);
    });
  }

  if (reviewPackageBtn) {
    reviewPackageBtn.addEventListener("click", () => {
      filesDisclosure.open = true;
      setWorkflowStep("package");
      filesDisclosure.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  if (openFolderBtn) {
    openFolderBtn.addEventListener("click", async () => {
      // Browsers can't reveal a local folder from a web page — the closest useful
      // action here is putting the output path on the clipboard for the user to paste.
      try {
        await navigator.clipboard.writeText(resultsPath.textContent || "");
        openFolderBtn.textContent = "Path copied";
      } catch {
        openFolderBtn.textContent = resultsPath.textContent || "Copy failed";
      }
      setTimeout(() => (openFolderBtn.textContent = "Open output folder"), 2000);
    });
  }

  // --- File selection -------------------------------------------------

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("drag-over");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("drag-over");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files?.[0];
    if (file) selectFile(file);
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) selectFile(file);
  });

  function selectFile(file) {
    selectedFile = file;
    const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
    fileName.textContent = `${file.name} — ${sizeMb} MB`;
    startBtn.disabled = false;
  }

  function applyAiEnabledState() {
    const enabled = aiToggle.checked;
    for (const field of [modelField, backendField]) field.style.opacity = enabled ? "1" : "0.4";
    modelSelect.disabled = !enabled;
    backendSelect.disabled = !enabled;
  }

  aiToggle.addEventListener("change", () => {
    applyAiEnabledState();
    if (aiToggle.checked) refreshModels();
  });
  backendSelect.addEventListener("change", refreshModels);
  applyAiEnabledState();
  refreshModels();

  document.getElementById("output-root").addEventListener("change", refreshSessions);
  refreshSessions();

  // --- Job submission ---------------------------------------------------

  startBtn.addEventListener("click", async () => {
    if (!selectedFile) return;
    // Only block if models exist but none is selected (shouldn't normally happen, since one is
    // auto-selected). If no local runtime/model is available at all, let it through — the backend
    // degrades to the same heuristic fallback the CLI uses without Ollama/MLX running.
    if (aiToggle.checked && modelsAvailable && !modelSelect.value) {
      showError(`No model selected for ${backendSelect.value}. Pick a model, or turn off "AI chapters & metadata".`);
      return;
    }

    const form = new FormData();
    form.append("video", selectedFile);
    form.append("output_root", document.getElementById("output-root").value.trim() || "youtube-ready-output");
    form.append("quality", document.getElementById("quality").value);
    form.append("language", document.getElementById("language").value.trim());
    form.append("translate", document.getElementById("translate").checked);
    form.append("ai", aiToggle.checked);
    form.append("metadata_model", modelSelect.value);
    form.append("metadata_backend", backendSelect.value);
    form.append("context", document.getElementById("context").value.trim());
    form.append("thumbnails", false);
    form.append("thumbnail_review", false);
    form.append("thumbnail_style_intent", thumbnailStyleSelect?.value || "balanced");

    log.innerHTML = "";
    progressTitle.textContent = "Uploading video…";
    resetBtn.hidden = true;
    showPanel(progressPanel);

    try {
      const response = await fetch("/api/jobs", { method: "POST", body: form });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `Upload failed (${response.status})`);
      }
      const data = await response.json();
      currentJobId = data.job_id;
      progressTitle.textContent = "Processing your video…";
      listenForProgress(currentJobId);
    } catch (err) {
      showError(err.message || String(err));
    }
  });

  function addLogLine(message) {
    for (const li of log.querySelectorAll("li.current")) li.classList.remove("current");
    const li = document.createElement("li");
    li.className = "current";
    li.textContent = message;
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  function listenForProgress(jobId) {
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === "progress") {
        addLogLine(payload.message);
      } else if (payload.type === "ai_status") {
        currentAiRuntime = payload.runtime || null;
        addLogLine(payload.runtime?.message || "AI runtime checked.");
      } else if (payload.type === "thumbnail_plan") {
        addLogLine("Thumbnail plan ready. Add supporting assets or continue rendering.");
        isAwaitingRender = true;
        showResults(payload.output_dir, payload.files, payload.thumbnails, payload.thumbnail_variants, payload.ai_runtime);
      } else if (payload.type === "done") {
        source.close();
        isAwaitingRender = false;
        showResults(payload.output_dir, payload.files, payload.thumbnails, payload.thumbnail_variants, payload.ai_runtime);
      } else if (payload.type === "error") {
        source.close();
        showError(payload.message);
      }
    };
    source.onerror = () => {
      source.close();
      fetch(`/api/jobs/${jobId}`)
        .then((r) => r.json())
        .then((data) => {
          if (data.status === "done") {
            isAwaitingRender = false;
            showResults(data.output_dir, data.files, data.thumbnails, data.thumbnail_variants, data.ai_runtime);
          }
          else if (data.status === "error") showError(data.error || "Connection lost.");
          else showError("Lost connection to the server.");
        })
        .catch(() => showError("Lost connection to the server."));
    };
  }

  function showError(message) {
    errorMessage.textContent = message;
    showPanel(errorPanel);
  }

  // --- Previous sessions ----------------------------------------------

  function currentOutputRoot() {
    return document.getElementById("output-root").value.trim() || "youtube-ready-output";
  }

  function formatSessionDate(epochSeconds) {
    try {
      return new Date(epochSeconds * 1000).toLocaleString();
    } catch {
      return "";
    }
  }

  async function refreshSessions() {
    let sessionsData = [];
    try {
      const response = await fetch(`/api/sessions?${new URLSearchParams({ output_root: currentOutputRoot() })}`);
      const data = await response.json();
      sessionsData = data.sessions || [];
    } catch {
      sessionsData = [];
    }

    sessionsPanel.hidden = uploadPanel.hidden || sessionsData.length === 0;
    sessionList.innerHTML = "";
    if (!sessionsData.length) return;

    for (const session of sessionsData) {
      const li = document.createElement("li");
      li.className = "session-row";

      const info = document.createElement("div");
      info.className = "session-info";
      const title = document.createElement("div");
      title.className = "session-title";
      title.textContent = session.title;
      title.title = session.title; // full name on hover/long-press, since the row truncates it
      const meta = document.createElement("div");
      meta.className = "hint";
      meta.textContent = formatSessionDate(session.created);
      info.appendChild(title);
      info.appendChild(meta);

      const actions = document.createElement("div");
      actions.className = "session-actions";

      const reopenBtn = document.createElement("button");
      reopenBtn.className = "ghost-btn small";
      reopenBtn.textContent = "Reopen";
      // Every row repeats "Reopen"/"Delete" — disambiguate for screen reader users
      // tabbing through a list of otherwise-identical button names.
      reopenBtn.setAttribute("aria-label", `Reopen "${session.title}"`);
      reopenBtn.addEventListener("click", () => reopenSession(session.name));

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "ghost-btn small danger";
      deleteBtn.textContent = "Delete";
      deleteBtn.setAttribute("aria-label", `Delete "${session.title}"`);
      deleteBtn.addEventListener("click", () => removeSession(session.name));

      actions.appendChild(reopenBtn);
      actions.appendChild(deleteBtn);

      li.appendChild(info);
      li.appendChild(actions);
      sessionList.appendChild(li);
    }
  }

  async function reopenSession(name) {
    try {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(name)}/load?${new URLSearchParams({ output_root: currentOutputRoot() })}`,
        { method: "POST" }
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `Could not reopen this session (${response.status})`);
      }
      const data = await response.json();
      currentJobId = data.id;
      isAwaitingRender = data.status === "waiting_for_thumbnail_assets";
      showResults(data.output_dir, data.files, data.thumbnails, data.thumbnail_variants, data.ai_runtime);
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function removeSession(name) {
    const confirmed = await openConfirmDialog({
      title: "Delete this session?",
      message: "This deletes the session's publishing pack. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!confirmed) return;
    try {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(name)}?${new URLSearchParams({ output_root: currentOutputRoot() })}`,
        { method: "DELETE" }
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `Could not delete this session (${response.status})`);
      }
      refreshSessions();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  // --- Results ------------------------------------------------------

  const PREVIEW_TAB = "▶ Preview & edit subtitles";

  let currentThumbnails = null;
  let currentThumbnailVariants = null;
  let currentAiRuntime = null;
  // True only while the backend job is in "waiting_for_thumbnail_assets" (after
  // a "thumbnail_plan" SSE event, a manual "Generate thumbnail plans", or
  // reopening a session whose plans were never rendered) — the only state in
  // which POST /continue is accepted. A missing rendered_filename alone isn't
  // enough: that state is what the server checks.
  let isAwaitingRender = false;
  // Title/overlay text only save on blur or Enter (see buildStudioEditor), so a
  // click on Render/Regenerate/Upload/switch-tab that fires before that blur
  // would otherwise silently discard whatever was just typed. Track the most
  // recent in-flight save here so those actions can flush it first.
  let pendingVariantSavePromise = Promise.resolve();

  async function flushPendingStudioEdits() {
    if (studioEditor && document.activeElement && studioEditor.contains(document.activeElement)) {
      document.activeElement.blur(); // synchronously fires "change" -> saveText() below
    }
    await pendingVariantSavePromise;
  }

  // Friendly labels for the bottom "Publishing package" checklist — keyed by
  // output filename so it reads as a package of deliverables, not a directory listing.
  const PACKAGE_ITEMS = [
    { match: /^titles\.txt$/, label: "Title" },
    { match: /^description\.txt$/, label: "Description" },
    { match: /^chapters\.txt$/, label: "Chapters" },
    { match: /^captions\.(srt|vtt)$/, label: "Captions" },
    { match: /^hashtags\.txt$/, label: "Hashtags" },
    { match: /^transcript\.(txt|json)$/, label: "Transcript" },
  ];

  function setWorkflowStep(step) {
    if (!workflowSteps) return;
    for (const li of workflowSteps.querySelectorAll(".workflow-step")) {
      const isActive = li.dataset.step === step;
      li.classList.toggle("active", isActive);
      // The active step is currently shown by color alone (.active); aria-current
      // gives screen reader / high-contrast users the same signal non-visually.
      if (isActive) li.setAttribute("aria-current", "step");
      else li.removeAttribute("aria-current");
    }
  }

  function renderPackageChecklist(files) {
    if (!packageChecklist) return;
    packageChecklist.innerHTML = "";
    const seen = new Set();
    const items = [];
    for (const { match, label } of PACKAGE_ITEMS) {
      if (seen.has(label)) continue;
      if (files.some((f) => match.test(f))) {
        items.push(label);
        seen.add(label);
      }
    }
    const hasThumbnail = !!(currentThumbnailVariants && currentThumbnailVariants.variants && currentThumbnailVariants.variants.some((v) => v.rendered_filename));
    items.push(hasThumbnail ? "Thumbnail" : null);
    for (const label of items) {
      if (!label) continue;
      const li = document.createElement("li");
      li.className = `package-item ${label === "Thumbnail" && !hasThumbnail ? "pending" : "ready"}`;
      li.textContent = label;
      packageChecklist.appendChild(li);
    }
    if (packagePanelCount) {
      const ready = items.filter(Boolean).length;
      packagePanelCount.textContent = `${ready} asset${ready === 1 ? "" : "s"} ready`;
    }
  }

  function showResults(outputDir, files, thumbnails, thumbnailVariants, aiRuntime = null) {
    resultsPath.textContent = outputDir;
    currentThumbnails = thumbnails || null;
    currentThumbnailVariants = thumbnailVariants || null;
    currentAiRuntime = aiRuntime || currentAiRuntime;
    tabsEl.innerHTML = "";
    tabsSecondaryEl.innerHTML = "";
    const visible = files.filter((f) => !HIDDEN_FROM_TABS.has(f));
    const previewable = visible.filter((f) => PREVIEWABLE.has(f));
    const rest = visible.filter((f) => !PREVIEWABLE.has(f));
    // The two workflow-stage tabs (review content, create thumbnail) stay in the
    // primary bar; every raw output file is secondary — tucked behind a disclosure
    // so the main screen reads as a workflow, not a directory listing.
    const primary = [PREVIEW_TAB, THUMBNAILS_TAB];
    const secondary = [...previewable, ...rest];

    primary.forEach((name, index) => {
      const tab = document.createElement("button");
      tab.className = "tab";
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", "false");
      tab.textContent = name;
      tab.addEventListener("click", () => selectTab(name));
      tabsEl.appendChild(tab);
      if (index === 0) selectTab(name);
    });
    secondary.forEach((name) => {
      const tab = document.createElement("button");
      tab.className = "tab";
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", "false");
      tab.textContent = name;
      tab.addEventListener("click", () => selectTab(name));
      tabsSecondaryEl.appendChild(tab);
    });

    renderPackageChecklist(visible);
    showPanel(resultsPanel);
  }

  async function selectTab(name) {
    currentTab = name;
    for (const tab of [...tabsEl.querySelectorAll(".tab"), ...tabsSecondaryEl.querySelectorAll(".tab")]) {
      const isActive = tab.textContent === name;
      tab.classList.toggle("active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
    }

    const isPreview = name === PREVIEW_TAB;
    const isThumbnails = name === THUMBNAILS_TAB;
    setWorkflowStep(isThumbnails ? "thumbnail" : "review");
    previewEditor.hidden = !isPreview;
    thumbnailStudio.hidden = !isThumbnails;
    viewer.hidden = isPreview || isThumbnails;
    tabToolbar.hidden = isPreview || isThumbnails;
    if (isPreview) {
      loadPreviewEditor();
      return;
    }
    if (isThumbnails) {
      renderThumbnailStudio();
      return;
    }

    const url = `/api/jobs/${currentJobId}/files/${encodeURIComponent(name)}`;
    downloadBtn.href = url;
    downloadBtn.setAttribute("download", name);
    viewer.textContent = "Loading…";
    try {
      const response = await fetch(url);
      const text = await response.text();
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`);
      }
      viewer.textContent = text;
    } catch (err) {
      console.error("Preview failed for", name, err);
      viewer.textContent = `Could not load a preview (${err.message || err}) — use Download instead.`;
    }
  }

  // The three options are the same video packaged three ways. The backend's
  // strategy keys (search/browse/outcome) are internal names — creators see
  // what each option is *for*.
  const STRATEGY_INFO = {
    search: { name: "Explain it", purpose: "Says plainly what the video is about. Best for people searching." },
    browse: { name: "Spark curiosity", purpose: "Makes people want to know more. Best for the home feed." },
    outcome: { name: "Show the result", purpose: "Leads with the payoff or the verdict." },
  };
  // Mirrors what render.py actually does per style (font size, backdrop
  // opacity, contrast), so the description matches the rendered result.
  const STYLE_INFO = {
    clear: { name: "Clear", description: "Smaller text, a light backdrop and a natural-looking picture." },
    balanced: { name: "Balanced", description: "Medium text with a modest contrast boost." },
    bold: { name: "Bold", description: "Big text, a darker backdrop and punchier contrast." },
  };
  const studioZoomState = {}; // variant_id -> last zoom value, so focal clicks reuse it
  let studioEmptyError = "";

  function strategyInfo(strategy) {
    return STRATEGY_INFO[strategy] || { name: strategy, purpose: "" };
  }

  function thumbUrl(filename, bust) {
    if (!filename) return "";
    const url = `/api/jobs/${currentJobId}/thumbnails/${encodeURIComponent(filename)}`;
    return bust ? `${url}?t=${Date.now()}` : url;
  }

  const ICON_PATHS = {
    close: "M6 6l12 12M18 6L6 18",
    download: "M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19.5h14",
    spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5",
  };

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.classList.add("icon");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICON_PATHS[name]);
    svg.appendChild(path);
    return svg;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function ghostButton(label, onClick, title) {
    const btn = el("button", "ghost-btn small", label);
    btn.type = "button";
    if (title) btn.title = title;
    btn.addEventListener("click", onClick);
    return btn;
  }

  // Which of the 3 options the single big editor is showing; persists across
  // re-renders (every edit re-renders) so saving doesn't snap back to option 1.
  let activeStudioVariantId = null;

  function describeProvenance(pkg) {
    if (pkg.fallback_reason) {
      const runtime = currentAiRuntime?.message ? ` (${currentAiRuntime.message})` : "";
      return `Ideas from built-in rules — the local AI model wasn't available${runtime}.`;
    }
    if (pkg.source === "llm" && pkg.model) return `Ideas by ${pkg.model}, running on this computer.`;
    return "Ideas from built-in rules.";
  }

  function renderThumbnailStudio() {
    studioSwitcher.innerHTML = "";
    studioEditor.innerHTML = "";
    const pkg = currentThumbnailVariants;
    if (!pkg || !pkg.variants || !pkg.variants.length) {
      studioProvenance.textContent = "";
      studioDownloadAll.hidden = true;
      studioSwitcher.hidden = true;
      studioEditor.classList.add("is-empty");
      studioEditor.appendChild(buildStudioEmptyState());
      return;
    }
    studioSwitcher.hidden = false;
    studioEditor.classList.remove("is-empty");
    const needsRender = isAwaitingRender && pkg.variants.some((v) => !v.rendered_filename);

    studioProvenance.textContent = describeProvenance(pkg);
    studioProvenance.title = pkg.fallback_reason || "";
    studioDownloadAll.hidden = needsRender || !pkg.variants.some((v) => v.rendered_filename);
    studioDownloadAll.href = `/api/jobs/${currentJobId}/thumbnail-variants/download-all`;
    studioDownloadAll.setAttribute("download", "thumbnails.zip");

    if (!pkg.variants.some((v) => v.variant_id === activeStudioVariantId)) {
      activeStudioVariantId = pkg.variants[0].variant_id;
    }
    studioSwitcher.setAttribute("role", "tablist");
    studioSwitcher.setAttribute("aria-label", "Thumbnail options");
    pkg.variants.forEach((variant, index) => studioSwitcher.appendChild(buildOptionTile(variant, index, pkg.variants)));

    const activeVariant = pkg.variants.find((v) => v.variant_id === activeStudioVariantId);
    studioEditor.dataset.variantId = activeVariant.variant_id;
    studioEditor.appendChild(buildStudioEditor(activeVariant, pkg.frames || [], needsRender));
  }

  function buildStudioEmptyState() {
    const wrap = el("div", "studio-empty");
    wrap.appendChild(el("h3", null, "Create thumbnail ideas"));
    wrap.appendChild(
      el(
        "p",
        null,
        "Picks strong moments from your video and suggests 3 thumbnails, each paired with one of your titles. You can change everything before anything is rendered."
      )
    );
    wrap.appendChild(el("p", "hint", "Uses ffmpeg and your local AI model, so it can take a few minutes. Progress shows here."));
    const btn = el("button", "primary-btn studio-empty-cta", "Create 3 thumbnail ideas");
    btn.type = "button";
    btn.id = "studio-generate-thumbnails";
    btn.addEventListener("click", generateThumbnailPlans);
    wrap.appendChild(btn);
    if (studioEmptyError) {
      const error = el("p", "studio-error", studioEmptyError);
      error.setAttribute("role", "alert");
      wrap.appendChild(error);
    }
    return wrap;
  }

  function buildOptionTile(variant, index, all) {
    const info = strategyInfo(variant.strategy);
    const isActive = variant.variant_id === activeStudioVariantId;
    const tile = el("button", "studio-option");
    tile.type = "button";
    tile.id = `studio-option-${variant.variant_id}`;
    tile.classList.toggle("active", isActive);
    tile.setAttribute("role", "tab");
    tile.setAttribute("aria-selected", String(isActive));
    tile.tabIndex = isActive ? 0 : -1;

    const media = el("span", "studio-option-media");
    const src = variant.rendered_filename
      ? thumbUrl(variant.rendered_filename, true)
      : variant.frame_filename
        ? thumbUrl(variant.frame_filename)
        : "";
    if (src) {
      const img = document.createElement("img");
      img.src = src;
      img.alt = "";
      media.appendChild(img);
    }
    tile.appendChild(media);

    const body = el("span", "studio-option-body");
    body.appendChild(el("span", "studio-option-name", info.name));
    body.appendChild(el("span", "studio-option-purpose", info.purpose));
    tile.appendChild(body);

    if (variant.warnings && variant.warnings.length) {
      const count = variant.warnings.length;
      tile.appendChild(el("span", "studio-option-flag", `${count} ${count === 1 ? "thing" : "things"} to check`));
    }

    tile.addEventListener("click", async () => {
      await flushPendingStudioEdits();
      activeStudioVariantId = variant.variant_id;
      renderThumbnailStudio();
      document.getElementById(`studio-option-${variant.variant_id}`)?.focus();
    });
    // Standard tablist keyboard model: arrows move between options.
    tile.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const step = event.key === "ArrowRight" ? 1 : -1;
      const next = all[(index + step + all.length) % all.length];
      document.getElementById(`studio-option-${next.variant_id}`)?.click();
    });
    return tile;
  }

  // A quality signal is "good" when it clears the readiness bar; anything else
  // (fair/poor/unknown) is flagged so the user knows where to look.
  function isSignalGood(value) {
    return value === "good" || value === "excellent" || value === "high";
  }

  function buildRecommendationChecklist(variant) {
    const assessment = variant.assessment || {};
    const values = [
      ["A person is clearly visible", assessment.person_visibility],
      ["The product is visible", assessment.product_visibility],
      ["The picture is sharp and well lit", assessment.frame_quality],
      ["The main subject is big and clear", assessment.subject_clarity],
      ["There's room for text", assessment.text_readability_potential],
    ].filter(([, value]) => value);
    if (!values.length) return null;
    const list = el("ul", "recommendation-checklist");
    for (const [label, value] of values) {
      const good = isSignalGood(value);
      const li = el("li", good ? "good" : "warn");
      li.appendChild(el("span", "check-mark", good ? "Yes" : "Weak"));
      li.appendChild(document.createTextNode(` ${label}`));
      list.appendChild(li);
    }
    return list;
  }

  function buildStudioEditor(variant, frames, needsRender = false) {
    // No wrapper div here: #studio-editor itself is the CSS grid (2 columns), so
    // the two columns must be appended directly as its children.
    const editor = document.createDocumentFragment();

    // One status line shared by every control for this option — every edit
    // auto-saves (and re-renders once rendered), so this is the single
    // "did my change take?" signal.
    const status = el("span", "studio-status");
    status.setAttribute("aria-live", "polite");

    const visual = el("div", "studio-editor-visual");
    visual.appendChild(buildPreview(variant, needsRender, status));
    visual.appendChild(buildBackgroundSection(variant, frames, status));
    editor.appendChild(visual);

    const fields = el("div", "studio-editor-fields");
    fields.appendChild(buildActionBar(variant, needsRender, status));
    fields.appendChild(buildTextSection(variant, status));
    fields.appendChild(buildStyleSection(variant, status));
    fields.appendChild(buildTitleSection(variant, status));
    fields.appendChild(buildExtraImagesSection(variant, status));
    fields.appendChild(buildFeedbackDisclosure(variant, status));
    editor.appendChild(fields);
    return editor;
  }

  function buildPreview(variant, needsRender, status) {
    const wrap = el("div", "studio-preview-wrap");
    const preview = el("div", "studio-preview");
    preview.tabIndex = 0;
    preview.setAttribute("role", "button");
    preview.setAttribute(
      "aria-label",
      "Thumbnail preview. Click a spot to keep it in the centre, or use the arrow keys to move the crop."
    );
    const img = document.createElement("img");
    // Rendered files keep the same name across edits, so bust the cache or the
    // browser keeps showing the image from before the edit.
    img.src = thumbUrl(variant.rendered_filename, true) || (variant.frame_filename ? thumbUrl(variant.frame_filename, true) : "");
    img.alt = variant.rendered_filename
      ? `Rendered thumbnail with the text “${variant.overlay_text || ""}”`
      : "Background picture for this thumbnail (not rendered yet)";
    preview.appendChild(img);
    if (!variant.rendered_filename) {
      preview.appendChild(el("span", "studio-preview-badge", needsRender ? "Draft — text appears when you render" : "Not rendered"));
    }
    wrap.appendChild(preview);

    const zoomRow = el("div", "studio-zoom");
    const zoomFieldId = `studio-zoom-${variant.variant_id}`;
    const zoomLabel = el("label", null, "Zoom");
    zoomLabel.htmlFor = zoomFieldId;
    const zoomInput = document.createElement("input");
    zoomInput.type = "range";
    zoomInput.id = zoomFieldId;
    // The backend's "zoom" is the fraction of the frame kept (1 = whole frame),
    // so invert it for the slider: right = closer in, like every zoom control.
    zoomInput.min = "0";
    zoomInput.max = "0.7";
    zoomInput.step = "0.05";
    const keptFraction = studioZoomState[variant.variant_id] ?? 1;
    zoomInput.value = String(Math.round((1 - keptFraction) * 100) / 100);
    const zoomValue = el("span", "studio-zoom-value");
    const paintZoomValue = () => (zoomValue.textContent = `${Math.round(100 / (1 - Number(zoomInput.value)))}%`);
    paintZoomValue();
    zoomInput.addEventListener("input", paintZoomValue);
    zoomInput.addEventListener("change", async () => {
      const kept = Math.round((1 - Number(zoomInput.value)) * 100) / 100;
      studioZoomState[variant.variant_id] = kept;
      await saveVariantEdit(
        variant.variant_id,
        { focal_x: variant.focal_x, focal_y: variant.focal_y, zoom: kept },
        status
      );
    });
    zoomRow.appendChild(zoomLabel);
    zoomRow.appendChild(zoomInput);
    zoomRow.appendChild(zoomValue);
    wrap.appendChild(zoomRow);
    wrap.appendChild(el("p", "hint studio-crop-hint", "Click the picture to choose what stays in the centre."));

    const setFocus = async (focalX, focalY) => {
      // At 100% the crop already fills the frame, so re-centring has no visible
      // effect — zoom in slightly the first time so a click always does something.
      const kept = studioZoomState[variant.variant_id] ?? 0.85;
      studioZoomState[variant.variant_id] = kept;
      await saveVariantEdit(
        variant.variant_id,
        { focal_x: Math.min(Math.max(focalX, 0), 1), focal_y: Math.min(Math.max(focalY, 0), 1), zoom: kept },
        status
      );
    };
    preview.addEventListener("click", (event) => {
      const rect = img.getBoundingClientRect();
      setFocus((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
    });
    preview.addEventListener("keydown", (event) => {
      const moves = { ArrowLeft: [-0.05, 0], ArrowRight: [0.05, 0], ArrowUp: [0, -0.05], ArrowDown: [0, 0.05] };
      const move = moves[event.key];
      if (!move) return;
      event.preventDefault();
      setFocus((variant.focal_x ?? 0.5) + move[0], (variant.focal_y ?? 0.5) + move[1]);
    });
    return wrap;
  }

  function buildBackgroundSection(variant, frames, status) {
    const section = el("section", "studio-section studio-background");
    section.appendChild(el("h3", "studio-section-heading", "Background picture"));

    const usable = frames.filter((frame) => !frame.is_duplicate && frame.filename);
    if (usable.length) {
      section.appendChild(el("p", "hint", "Pick a different moment from the video:"));
      const strip = el("div", "studio-frame-strip");
      strip.setAttribute("role", "list");
      for (const frame of usable) {
        const btn = el("button", "studio-frame");
        btn.type = "button";
        btn.setAttribute("role", "listitem");
        const isCurrent = variant.frame_filename === frame.filename;
        btn.classList.toggle("active", isCurrent);
        btn.setAttribute("aria-pressed", String(isCurrent));
        btn.setAttribute("aria-label", `Use the moment at ${formatDuration(frame.timestamp)}`);
        const img = document.createElement("img");
        img.src = thumbUrl(frame.preview_filename || frame.filename);
        img.alt = "";
        img.loading = "lazy";
        btn.appendChild(img);
        btn.appendChild(el("span", "studio-frame-time", formatDuration(frame.timestamp)));
        btn.addEventListener("click", () => saveVariantEdit(variant.variant_id, { frame_filename: frame.filename }, status));
        strip.appendChild(btn);
      }
      section.appendChild(strip);
    }

    const actions = el("div", "studio-actions");
    if (variant.frame_timestamp != null) {
      actions.appendChild(
        ghostButton(
          "Find a better take nearby",
          () => findBetterTake(variant.variant_id, status),
          "Looks a couple of seconds either side for a sharper frame with a better expression or eye contact"
        )
      );
    }
    actions.appendChild(buildUploadLabel(variant, status, { supporting: false, label: "Use my own photo…" }));
    if (variant.frame_timestamp == null && variant.frame_filename) {
      const remove = ghostButton("Remove my photo", () =>
        removeUploadedImage(variant.variant_id, fileBasename(variant.frame_filename), "uploaded photo", status)
      );
      remove.classList.add("danger");
      remove.title = "Delete this photo and go back to a moment from the video";
      actions.appendChild(remove);
    }
    section.appendChild(actions);
    return section;
  }

  function buildActionBar(variant, needsRender, status) {
    const bar = el("div", "studio-action-bar");
    if (needsRender) {
      const renderBtn = el("button", "primary-btn", "Render all 3 thumbnails");
      renderBtn.id = "studio-continue-render";
      renderBtn.type = "button";
      renderBtn.addEventListener("click", continueThumbnailRendering);
      bar.appendChild(renderBtn);
      bar.appendChild(el("p", "hint", "Adds the text and effects to every option. Takes a few seconds, and you can keep editing afterwards."));
    } else if (variant.rendered_filename) {
      const download = el("a", "primary-btn");
      download.appendChild(icon("download"));
      download.appendChild(document.createTextNode("Download this thumbnail"));
      download.href = thumbUrl(variant.rendered_filename);
      download.setAttribute("download", `thumbnail-${variant.strategy}.jpg`);
      bar.appendChild(download);
      const secondary = el("div", "studio-actions");
      secondary.appendChild(
        ghostButton(
          "New AI version of this option",
          () => regenerateVariant(variant.variant_id, status),
          "Asks the model for fresh text and a different moment for this option only. The other two options are left alone."
        )
      );
      secondary.appendChild(
        ghostButton(
          "Mark as the one I published",
          () => selectVariantForTest(variant.variant_id, status),
          "Keeps a local note of which option you published"
        )
      );
      bar.appendChild(secondary);
    }
    bar.appendChild(status);
    return bar;
  }

  function buildTextSection(variant, status) {
    const section = el("section", "studio-section");
    const fieldId = `studio-overlay-${variant.variant_id}`;
    const label = el("label", "studio-label", "Text on the thumbnail");
    label.htmlFor = fieldId;
    section.appendChild(label);
    const input = document.createElement("input");
    input.type = "text";
    input.id = fieldId;
    input.value = variant.overlay_text;
    input.placeholder = "No text — the picture speaks for itself";
    input.setAttribute("aria-describedby", `${fieldId}-hint`);
    section.appendChild(input);
    const hint = el("p", "hint", "A few words that add to the title rather than repeat it. Saves when you press Enter or click away.");
    hint.id = `${fieldId}-hint`;
    section.appendChild(hint);
    bindAutosave(input, () => input.value !== variant.overlay_text, () => ({ overlay_text: input.value }), variant, status);

    const ideas = el("div", "studio-ideas");
    const options = variant.overlay_options || [];
    if (options.length) {
      ideas.appendChild(el("span", "studio-ideas-label", "Other ideas — click one to use it:"));
      const list = el("div", "studio-idea-list");
      for (const idea of options) {
        const chip = el("button", "studio-idea", idea);
        chip.type = "button";
        const isCurrent = idea === variant.overlay_text;
        chip.classList.toggle("active", isCurrent);
        chip.setAttribute("aria-pressed", String(isCurrent));
        chip.addEventListener("click", async () => {
          await flushPendingStudioEdits();
          saveVariantEdit(variant.variant_id, { overlay_text: idea }, status);
        });
        list.appendChild(chip);
      }
      ideas.appendChild(list);
    }
    const more = ghostButton(options.length ? "More ideas" : "Suggest other text", () => suggestOverlayIdeas(variant.variant_id, status));
    more.prepend(icon("spark"));
    more.classList.add("with-icon");
    ideas.appendChild(more);
    section.appendChild(ideas);

    if (variant.warnings && variant.warnings.length) {
      const box = el("div", "studio-warnings");
      box.appendChild(el("p", "studio-warnings-title", "Worth checking"));
      const list = el("ul");
      for (const warning of variant.warnings) list.appendChild(el("li", null, warning));
      box.appendChild(list);
      section.appendChild(box);
    }
    return section;
  }

  function buildStyleSection(variant, status) {
    const section = el("section", "studio-section");
    const legendId = `studio-style-legend-${variant.variant_id}`;
    const legend = el("span", "studio-label", "Text size & contrast");
    legend.id = legendId;
    section.appendChild(legend);
    const options = el("div", "studio-segmented");
    // Plain toggle buttons with radiogroup semantics for screen readers.
    options.setAttribute("role", "radiogroup");
    options.setAttribute("aria-labelledby", legendId);
    const current = STYLE_INFO[variant.style_intent] ? variant.style_intent : "balanced";
    for (const [value, info] of Object.entries(STYLE_INFO)) {
      const btn = el("button", null, info.name);
      btn.type = "button";
      btn.setAttribute("role", "radio");
      const isActive = current === value;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-checked", String(isActive));
      btn.addEventListener("click", () => {
        if (!isActive) saveVariantEdit(variant.variant_id, { style_intent: value }, status);
      });
      options.appendChild(btn);
    }
    section.appendChild(options);
    section.appendChild(el("p", "hint", STYLE_INFO[current].description));
    return section;
  }

  function buildTitleSection(variant, status) {
    const section = el("section", "studio-section");
    const fieldId = `studio-title-${variant.variant_id}`;
    const label = el("label", "studio-label", "Title it's paired with");
    label.htmlFor = fieldId;
    section.appendChild(label);
    const input = document.createElement("input");
    input.type = "text";
    input.id = fieldId;
    input.value = variant.title;
    input.setAttribute("aria-describedby", `${fieldId}-hint`);
    section.appendChild(input);
    const hint = el("p", "hint", "This option was designed around this title. Changing it here only affects this option.");
    hint.id = `${fieldId}-hint`;
    section.appendChild(hint);
    bindAutosave(input, () => input.value !== variant.title, () => ({ title: input.value }), variant, status);
    return section;
  }

  // Text fields save on blur/Enter (not per keystroke — each save re-renders).
  function bindAutosave(input, isDirty, buildUpdate, variant, status) {
    input.addEventListener("change", () => {
      if (!isDirty()) return;
      pendingVariantSavePromise = saveVariantEdit(variant.variant_id, buildUpdate(), status);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
    });
  }

  function buildExtraImagesSection(variant, status) {
    const section = el("section", "studio-section");
    section.appendChild(el("h3", "studio-section-heading", "Extra images"));
    section.appendChild(
      el(
        "p",
        "hint",
        "Optional: a product shot, logo or screenshot. It's added when the layout has room for it, and it's shared by all 3 options."
      )
    );
    const rec = variant.assessment?.recommended_uploads?.[0];
    if (rec && rec.reason) section.appendChild(el("p", "studio-suggestion", `Suggestion: ${rec.reason}`));

    const assets = (variant.source_assets?.uploaded_assets || []).filter(
      // The option's own photo is managed under "Background picture".
      (asset) => fileBasename(asset.path) !== fileBasename(variant.frame_filename)
    );
    if (assets.length) {
      const list = el("ul", "studio-asset-list");
      for (const asset of assets) {
        const role = (asset.role || "other").replaceAll("_", " ");
        const name = asset.original_filename || "uploaded image";
        const item = el("li", "studio-asset");
        const text = el("span", "studio-asset-label");
        text.appendChild(el("span", "studio-asset-role", role));
        text.appendChild(el("span", "studio-asset-name", name));
        text.title = asset.reason || `${role}: ${name}`;
        item.appendChild(text);
        const remove = el("button", "studio-asset-remove");
        remove.type = "button";
        remove.appendChild(icon("close"));
        remove.setAttribute("aria-label", `Remove ${name}`);
        remove.title = "Remove this image";
        remove.addEventListener("click", () =>
          removeUploadedImage(variant.variant_id, fileBasename(asset.path), `${role} image “${name}”`, status)
        );
        item.appendChild(remove);
        list.appendChild(item);
      }
      section.appendChild(list);
    }
    const actions = el("div", "studio-actions");
    actions.appendChild(buildUploadLabel(variant, status, { supporting: true, label: "Add an image…" }));
    section.appendChild(actions);
    return section;
  }

  function buildFeedbackDisclosure(variant, status) {
    const details = el("details", "studio-details");
    details.appendChild(el("summary", null, "AI feedback on this option"));

    const checklist = buildRecommendationChecklist(variant);
    if (checklist) details.appendChild(checklist);
    if (variant.advice || variant.assessment) details.appendChild(buildThumbnailAdvice(variant));
    const recheckPanel = buildRecheckPanel(variant);
    if (recheckPanel) details.appendChild(recheckPanel);

    const actions = el("div", "studio-actions");
    if (variant.rendered_filename) {
      actions.appendChild(
        ghostButton(
          "Ask AI to review the finished thumbnail",
          () => recheckVariant(variant.variant_id, status),
          "Needs a vision-capable model. Looks at the rendered image, text and all."
        )
      );
    }
    if (variant.edit_prompt) {
      actions.appendChild(
        ghostButton(
          "Copy a prompt for an image editor",
          async () => {
            await navigator.clipboard.writeText(variant.edit_prompt);
            status.textContent = "Prompt copied — paste it into an AI image editor.";
          },
          "Instructions you can give an AI image editor to polish this thumbnail without changing the facts"
        )
      );
    }
    if (actions.children.length) details.appendChild(actions);
    if (variant.composition) details.appendChild(buildCompositionSummary(variant.composition));
    return details;
  }

  function buildRecheckPanel(variant) {
    if (!variant.recheck) return null;
    const section = document.createElement("div");
    section.className = "studio-diagnostic studio-recheck";

    const heading = document.createElement("h3");
    heading.textContent = "AI review of the finished thumbnail";
    section.appendChild(heading);

    const badge = document.createElement("span");
    badge.className = "readiness-pill good";
    badge.textContent = "Based on the rendered image";
    section.appendChild(badge);

    appendDiagnosticList(section, "Strengths", variant.recheck.strengths || []);
    appendDiagnosticList(section, "Still weak", variant.recheck.weaknesses || []);

    if (variant.recheck.highest_value_change) {
      const best = document.createElement("p");
      best.className = "studio-best-improvement";
      best.textContent = `Best next improvement: ${variant.recheck.highest_value_change}`;
      section.appendChild(best);
    }
    return section;
  }

  // --- Studio activity indicator ---------------------------------------
  // Thumbnail-studio actions are single blocking requests that can take
  // minutes (local model load + LLM + vision calls). While one is in flight,
  // poll the job's activity endpoint so the user sees which step is running
  // and how long it's been going, instead of a static "Generating…".

  function formatDuration(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  async function withStudioActivity(fallbackMessage, request) {
    const startedAt = Date.now();
    let message = fallbackMessage;
    let stepElapsed = null;
    const paint = () => {
      const total = (Date.now() - startedAt) / 1000;
      if (studioActivityText.textContent !== message) studioActivityText.textContent = message;
      const parts = [`${formatDuration(total)} elapsed`];
      if (stepElapsed !== null && stepElapsed + 1 < total) parts.push(`this step ${formatDuration(stepElapsed)}`);
      if (stepElapsed !== null && stepElapsed > 60) {
        parts.push("local models can take a few minutes — it's still working");
      }
      studioActivityTime.textContent = parts.join(" · ");
    };
    const poll = async () => {
      try {
        const response = await fetch(`/api/jobs/${currentJobId}/activity`);
        if (!response.ok) return;
        const activity = await response.json();
        if (activity.active && activity.message) {
          message = activity.message;
          stepElapsed = activity.step_elapsed;
        }
      } catch {
        // Polling is best-effort; the request itself reports real failures.
      }
    };
    studioActivity.hidden = false;
    paint();
    poll().then(paint);
    const ticker = setInterval(paint, 1000);
    const poller = setInterval(() => poll().then(paint), 1500);
    try {
      return await request();
    } finally {
      clearInterval(ticker);
      clearInterval(poller);
      studioActivity.hidden = true;
      studioActivityText.textContent = "";
      studioActivityTime.textContent = "";
    }
  }

  async function recheckVariant(variantId, status) {
    await flushPendingStudioEdits();
    status.textContent = "Asking the AI to review the thumbnail…";
    try {
      const response = await withStudioActivity("Asking the AI to review the thumbnail…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/recheck`, {
          method: "POST",
        })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Review ready — see “AI feedback on this option”.");
    } catch (err) {
      status.textContent = `Couldn't review the thumbnail (${err.message || err}).`;
    }
  }

  async function findBetterTake(variantId, status) {
    await flushPendingStudioEdits();
    status.textContent = "Looking for a better take nearby…";
    try {
      const response = await withStudioActivity("Looking for a better take nearby…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/best-take`, {
          method: "POST",
        })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Switched to a better take.");
    } catch (err) {
      status.textContent = `Could not find a better take (${err.message || err}).`;
    }
  }

  function buildUploadLabel(variant, status, options = {}) {
    const uploadLabel = el("label", "ghost-btn small", options.label);
    const uploadInput = document.createElement("input");
    uploadInput.type = "file";
    uploadInput.accept = "image/*";
    uploadInput.hidden = true;
    uploadInput.addEventListener("change", () => {
      if (!uploadInput.files?.[0]) return;
      if (options.supporting) uploadSupportingAsset(variant.variant_id, uploadInput.files[0], status);
      else uploadVariantImage(variant.variant_id, uploadInput.files[0], status);
    });
    uploadLabel.appendChild(uploadInput);
    return uploadLabel;
  }

  function fileBasename(path) {
    return String(path || "").split(/[\\/]/).pop();
  }

  async function suggestOverlayIdeas(variantId, status) {
    await flushPendingStudioEdits();
    status.textContent = "Finding text ideas…";
    try {
      const response = await withStudioActivity("Finding text ideas…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/overlay-ideas`, {
          method: "POST",
        })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "New ideas below the text box.");
    } catch (err) {
      status.textContent = `Couldn't get text ideas (${err.message || err}).`;
    }
  }

  function buildThumbnailAdvice(variant) {
    const section = document.createElement("div");
    section.className = "studio-diagnostic";

    const advice = variant.advice || {};
    const assessment = variant.assessment || {};
    const overall = document.createElement("p");
    overall.textContent = advice.overall || "This package uses the available source frame and keeps the thumbnail truthful.";
    section.appendChild(overall);

    appendDiagnosticList(section, "Strengths", advice.strengths || assessment.strengths || []);
    appendDiagnosticList(section, "Could be better", advice.weaknesses || assessment.weaknesses || []);

    const best = advice.highest_value_change || assessment.highest_value_change;
    if (best) {
      const improvement = document.createElement("p");
      improvement.className = "studio-best-improvement";
      improvement.textContent = `Biggest improvement: ${best}`;
      section.appendChild(improvement);
    }
    if ((advice.strengths && advice.strengths.length) || (advice.weaknesses && advice.weaknesses.length)) {
      const critique = document.createElement("p");
      critique.className = "studio-critique-note";
      critique.textContent = "This is design feedback, not a prediction of how the video will perform.";
      section.appendChild(critique);
    }
    return section;
  }

  function buildCompositionSummary(composition) {
    const section = document.createElement("div");
    section.className = "studio-diagnostic compact";
    const heading = document.createElement("h3");
    heading.textContent = "How the AI planned the layout";
    section.appendChild(heading);
    const rows = [
      ["Main focus", composition.visual_anchor],
      ["Layout", (composition.layout || "").replaceAll("_", " ")],
      ["Crop", composition.crop_instruction],
      ["Background", (composition.background_treatment || "").replaceAll("_", " ")],
    ];
    for (const [label, value] of rows) {
      if (!value) continue;
      const row = document.createElement("p");
      row.textContent = `${label}: ${value}`;
      section.appendChild(row);
    }
    return section;
  }

  function appendDiagnosticList(section, label, items) {
    if (!items || !items.length) return;
    const title = document.createElement("h4");
    title.textContent = label;
    section.appendChild(title);
    const list = document.createElement("ul");
    for (const item of items.slice(0, 3)) {
      const li = document.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    }
    section.appendChild(list);
  }

  function flashStatus(variantId, message) {
    // renderThumbnailStudio() below rebuilds every card from scratch, which replaces
    // the status <span> the caller was holding a reference to — so a message set just
    // before that rebuild is instantly wiped out before anyone sees it. Find the fresh
    // status element for this variant after the rebuild and flash the message there.
    if (studioEditor.dataset.variantId !== variantId) return; // the user switched tabs mid-request
    const freshStatus = studioEditor.querySelector(".studio-status");
    if (!freshStatus) return;
    freshStatus.textContent = message;
    if (message) setTimeout(() => (freshStatus.textContent === message ? (freshStatus.textContent = "") : null), 2000);
  }

  async function saveVariantEdit(variantId, updates, status) {
    if (status) status.textContent = "Saving…";
    try {
      const response = await fetch(`/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Saved.");
    } catch (err) {
      if (status) status.textContent = `Could not save (${err.message || err}).`;
    }
  }

  async function regenerateVariant(variantId, status) {
    await flushPendingStudioEdits();
    status.textContent = "Asking for a new version…";
    try {
      const response = await withStudioActivity("Asking for a new version of this option…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/regenerate`, {
          method: "POST",
        })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "New version ready.");
    } catch (err) {
      status.textContent = `Could not regenerate (${err.message || err}).`;
    }
  }

  async function uploadVariantImage(variantId, file, status) {
    await flushPendingStudioEdits();
    status.textContent = "Uploading your photo…";
    const form = new FormData();
    form.append("image", file);
    try {
      const response = await withStudioActivity("Uploading your photo…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/upload`, {
          method: "POST",
          body: form,
        })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Your photo is now the background.");
    } catch (err) {
      status.textContent = `Could not upload (${err.message || err}).`;
    }
  }

  async function uploadSupportingAsset(variantId, file, status) {
    await flushPendingStudioEdits();
    status.textContent = "Adding image…";
    const form = new FormData();
    form.append("image", file);
    try {
      const response = await withStudioActivity("Adding the image…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/assets`, {
          method: "POST",
          body: form,
        })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Image added.");
    } catch (err) {
      status.textContent = `Couldn't add the image (${err.message || err}).`;
    }
  }

  async function removeUploadedImage(variantId, filename, description, status) {
    const confirmed = await openConfirmDialog({
      title: "Remove this image?",
      message: `This deletes the ${description} from all 3 options. Any rendered thumbnails are rebuilt without it.`,
      confirmLabel: "Remove",
      danger: true,
    });
    if (!confirmed) return;
    await flushPendingStudioEdits();
    status.textContent = "Removing…";
    try {
      const response = await withStudioActivity("Removing image…", () =>
        fetch(
          `/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/assets/${encodeURIComponent(filename)}`,
          { method: "DELETE" }
        )
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Image removed.");
    } catch (err) {
      status.textContent = `Could not remove image (${err.message || err}).`;
    }
  }

  async function continueThumbnailRendering() {
    await flushPendingStudioEdits();
    const btn = document.getElementById("studio-continue-render");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Rendering…";
    }
    try {
      const response = await withStudioActivity("Rendering thumbnails…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/continue`, { method: "POST" })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      currentThumbnailVariants = data.thumbnail_variants || currentThumbnailVariants;
      renderThumbnailStudio();
    } catch (err) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Render all 3 thumbnails";
      }
      const status = studioEditor.querySelector(".studio-status");
      if (status) status.textContent = `Couldn't render the thumbnails (${err.message || err}).`;
    }
  }

  async function generateThumbnailPlans() {
    const btn = document.getElementById("studio-generate-thumbnails");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Creating ideas…";
    }
    studioEmptyError = "";
    try {
      const response = await withStudioActivity("Creating thumbnail ideas…", () =>
        fetch(`/api/jobs/${currentJobId}/thumbnail-variants/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ style_intent: thumbnailStyleSelect?.value || "balanced" }),
        })
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      currentThumbnails = data.thumbnails || null;
      currentThumbnailVariants = data.thumbnail_variants || null;
      currentAiRuntime = data.ai_runtime || currentAiRuntime;
      isAwaitingRender = true;
      renderThumbnailStudio();
    } catch (err) {
      studioEmptyError = `Couldn't create thumbnail ideas (${err.message || err}). Check the video is still in the session folder, then try again.`;
      renderThumbnailStudio();
    }
  }

  async function selectVariantForTest(variantId, status) {
    const result = await openConfirmDialog({
      title: "Mark this as the thumbnail you published?",
      message:
        "Keeps a note on this computer of which option you used, so you can compare later. If you ran a YouTube “Test & Compare”, you can add the result too.",
      confirmLabel: "Save note",
      showInput: true,
      inputLabel: "Test result or note (optional)",
      placeholder: "e.g. Won Test & Compare with 54% watch time share",
    });
    if (result === null) return;
    status.textContent = "Saving…";
    try {
      const response = await fetch(
        `/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/select`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ youtube_test_result: result || null }),
        }
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      status.textContent = "Saved — marked as published.";
      setTimeout(() => (status.textContent = ""), 1500);
    } catch (err) {
      status.textContent = `Couldn't save the note (${err.message || err}).`;
    }
  }

  // --- Generic confirm/prompt dialog -------------------------------------
  // Replaces window.confirm()/prompt(): those native dialogs are the only
  // unstyled, unlabeled UI in the app, breaking the custom look everywhere
  // else and giving prompt() no room to explain what it's asking for. This
  // reuses the same focus-trap pattern as the branding modal below.

  const confirmBackdrop = document.getElementById("confirm-backdrop");
  const confirmModal = document.getElementById("confirm-modal");
  const confirmTitle = document.getElementById("confirm-title");
  const confirmMessage = document.getElementById("confirm-message");
  const confirmInputField = document.getElementById("confirm-input-field");
  const confirmInputLabel = document.getElementById("confirm-input-label");
  const confirmInput = document.getElementById("confirm-input");
  const confirmCancelBtn = document.getElementById("confirm-cancel-btn");
  const confirmOkBtn = document.getElementById("confirm-ok-btn");

  function focusableIn(container) {
    return Array.from(
      container.querySelectorAll('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')
    ).filter((el) => !el.disabled && el.getAttribute("aria-hidden") !== "true" && el.offsetParent !== null);
  }

  let confirmReturnFocus = null;
  let confirmResolve = null;

  function resolveConfirmDialog(value) {
    confirmBackdrop.hidden = true;
    document.removeEventListener("keydown", trapConfirmTab);
    const resolve = confirmResolve;
    confirmResolve = null;
    if (confirmReturnFocus) confirmReturnFocus.focus();
    if (resolve) resolve(value);
  }

  function trapConfirmTab(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      resolveConfirmDialog(null);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableIn(confirmModal);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  confirmCancelBtn.addEventListener("click", () => resolveConfirmDialog(null));
  confirmOkBtn.addEventListener("click", () => resolveConfirmDialog(confirmInputField.hidden ? true : confirmInput.value));
  confirmBackdrop.addEventListener("click", (event) => {
    if (event.target === confirmBackdrop) resolveConfirmDialog(null);
  });
  confirmInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      confirmOkBtn.click();
    }
  });

  // Resolves to `true`/`null` for a plain confirm, or the typed string/`null` when showInput is set.
  function openConfirmDialog({
    title,
    message,
    confirmLabel = "OK",
    cancelLabel = "Cancel",
    danger = false,
    showInput = false,
    inputLabel = "Note",
    placeholder = "",
  }) {
    return new Promise((resolve) => {
      confirmReturnFocus = document.activeElement;
      confirmResolve = resolve;
      confirmTitle.textContent = title;
      confirmMessage.textContent = message;
      confirmInputField.hidden = !showInput;
      confirmInputLabel.textContent = inputLabel;
      confirmInput.value = "";
      confirmInput.placeholder = placeholder;
      confirmOkBtn.textContent = confirmLabel;
      confirmOkBtn.className = danger ? "ghost-btn small danger" : "primary-btn small";
      confirmCancelBtn.textContent = cancelLabel;
      confirmBackdrop.hidden = false;
      document.addEventListener("keydown", trapConfirmTab);
      (showInput ? confirmInput : confirmOkBtn).focus();
    });
  }

  // --- Branding presets -------------------------------------------------

  const brandingBtn = document.getElementById("branding-btn");
  const brandingBackdrop = document.getElementById("branding-backdrop");
  const brandingModal = document.getElementById("branding-modal");
  const brandingCancel = document.getElementById("branding-cancel");
  const brandingSave = document.getElementById("branding-save");
  const brandingStatus = document.getElementById("branding-status");
  const brandChannelName = document.getElementById("brand-channel-name");
  const brandAudience = document.getElementById("brand-audience");
  const brandTone = document.getElementById("brand-tone");
  const brandPrimaryColor = document.getElementById("brand-primary-color");
  const brandAccentColor = document.getElementById("brand-accent-color");
  const brandEmphasis = document.getElementById("brand-emphasis");
  const brandTextDensity = document.getElementById("brand-text-density");
  const brandProhibited = document.getElementById("brand-prohibited");

  // Font picker: a single text input with a <datalist> of known font names (bundled
  // default + any the creator has added). Typing filters the browser's native
  // suggestion popup; picking one (or typing an exact name) selects that font.
  // Anything that doesn't match a known name falls back to the bundled default.
  const brandFontInput = document.getElementById("brand-font-input");
  const brandFontList = document.getElementById("brand-font-list");
  const brandFontUpload = document.getElementById("brand-font-upload");
  const brandFontStatus = document.getElementById("brand-font-status");

  const brandLogoUpload = document.getElementById("brand-logo-upload");
  const brandLogoStatus = document.getElementById("brand-logo-status");
  const brandLogoImg = document.getElementById("brand-logo-img");
  const brandLogoEmpty = document.getElementById("brand-logo-empty");
  const brandLogoRemove = document.getElementById("brand-logo-remove");

  let availableFonts = [];

  function fontByName(name) {
    const normalized = (name || "").trim().toLowerCase();
    return availableFonts.find((f) => f.name.toLowerCase() === normalized);
  }

  function selectedFont() {
    return fontByName(brandFontInput.value);
  }

  function updateFontStatus() {
    if (!brandFontInput.value.trim()) {
      brandFontStatus.textContent = "Using the default font.";
    } else if (selectedFont()) {
      brandFontStatus.textContent = "";
    } else {
      brandFontStatus.textContent = "No exact match — pick a suggestion from the list, or the default will be used.";
    }
  }

  function populateFontDatalist() {
    brandFontList.innerHTML = "";
    for (const font of availableFonts) {
      const option = document.createElement("option");
      option.value = font.name;
      brandFontList.appendChild(option);
    }
  }

  async function refreshFonts(selectedName) {
    try {
      const response = await fetch("/api/branding/fonts");
      const data = await response.json();
      availableFonts = data.fonts || [];
      populateFontDatalist();
      if (selectedName !== undefined) brandFontInput.value = selectedName || "";
      updateFontStatus();
    } catch (err) {
      brandFontStatus.textContent = `Could not load fonts (${err.message || err}).`;
    }
  }

  brandFontInput.addEventListener("input", updateFontStatus);

  brandFontUpload.addEventListener("change", async () => {
    const file = brandFontUpload.files?.[0];
    if (!file) return;
    brandFontStatus.textContent = "Adding font…";
    const form = new FormData();
    form.append("font", file);
    try {
      const response = await fetch("/api/branding/fonts", { method: "POST", body: form });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      availableFonts = data.fonts || [];
      populateFontDatalist();
      const added = availableFonts[availableFonts.length - 1];
      brandFontInput.value = added?.name || "";
      brandFontStatus.textContent = "Font added and selected.";
    } catch (err) {
      brandFontStatus.textContent = `Could not add font (${err.message || err}).`;
    }
    brandFontUpload.value = "";
  });

  function refreshLogoPreview(hasLogo) {
    if (hasLogo) {
      brandLogoImg.src = `/api/branding/logo?t=${Date.now()}`;
      brandLogoImg.alt = "Current channel logo";
      brandLogoImg.hidden = false;
      brandLogoEmpty.hidden = true;
      brandLogoRemove.hidden = false;
    } else {
      brandLogoImg.hidden = true;
      brandLogoImg.removeAttribute("src");
      brandLogoEmpty.hidden = false;
      brandLogoRemove.hidden = true;
    }
  }

  brandLogoUpload.addEventListener("change", async () => {
    const file = brandLogoUpload.files?.[0];
    if (!file) return;
    brandLogoStatus.textContent = "Uploading…";
    const form = new FormData();
    form.append("logo", file);
    try {
      const response = await fetch("/api/branding/logo", { method: "POST", body: form });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      refreshLogoPreview(true);
      brandLogoStatus.textContent = "Logo saved.";
      setTimeout(() => (brandLogoStatus.textContent = ""), 1500);
    } catch (err) {
      brandLogoStatus.textContent = `Could not upload logo (${err.message || err}).`;
    }
    brandLogoUpload.value = "";
  });

  brandLogoRemove.addEventListener("click", async () => {
    brandLogoStatus.textContent = "Removing…";
    try {
      const response = await fetch("/api/branding/logo", { method: "DELETE" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      refreshLogoPreview(false);
      brandLogoStatus.textContent = "";
    } catch (err) {
      brandLogoStatus.textContent = `Could not remove logo (${err.message || err}).`;
    }
  });

  // --- Focus trap for the branding modal ---------------------------------
  // Keeps Tab/Shift+Tab cycling within the modal while it's open, returns
  // focus to whatever opened it on close, and closes on Escape — the
  // baseline behavior expected of any modal dialog.
  let brandingReturnFocus = null;

  function trapBrandingTab(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeBrandingModal();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableIn(brandingModal);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function closeBrandingModal() {
    brandingBackdrop.hidden = true;
    document.removeEventListener("keydown", trapBrandingTab);
    if (brandingReturnFocus) brandingReturnFocus.focus();
  }

  async function openBrandingModal() {
    brandingReturnFocus = document.activeElement;
    brandingBackdrop.hidden = false;
    document.addEventListener("keydown", trapBrandingTab);
    brandChannelName.focus();
    brandingStatus.textContent = "Loading…";
    try {
      const response = await fetch("/api/branding");
      const profile = await response.json();
      brandChannelName.value = profile.channel_name || "";
      brandAudience.value = profile.audience || "";
      brandTone.value = profile.tone || "";
      brandPrimaryColor.value = profile.primary_color || "#111318";
      brandAccentColor.value = profile.accent_color || "#ffffff";
      brandEmphasis.value = profile.emphasis || "balanced";
      brandTextDensity.value = profile.text_density || "balanced";
      brandProhibited.value = (profile.prohibited_phrases || []).join("\n");
      refreshLogoPreview(Boolean(profile.logo_path));
      await refreshFonts(profile.font_path ? profile.font_name : "");
      brandingStatus.textContent = "";
    } catch (err) {
      brandingStatus.textContent = `Could not load branding (${err.message || err}).`;
    }
  }

  brandingBtn.addEventListener("click", openBrandingModal);
  brandingCancel.addEventListener("click", closeBrandingModal);
  brandingBackdrop.addEventListener("click", (event) => {
    if (event.target === brandingBackdrop) closeBrandingModal();
  });

  brandingSave.addEventListener("click", async () => {
    brandingStatus.textContent = "Saving…";
    const font = selectedFont();
    const profile = {
      channel_name: brandChannelName.value.trim(),
      audience: brandAudience.value.trim(),
      tone: brandTone.value.trim(),
      primary_color: brandPrimaryColor.value,
      accent_color: brandAccentColor.value,
      emphasis: brandEmphasis.value,
      text_density: brandTextDensity.value,
      font_path: font ? font.path : null,
      font_name: font ? font.name : "DejaVu Sans Bold (default)",
      prohibited_phrases: brandProhibited.value.split("\n").map((s) => s.trim()).filter(Boolean),
    };
    try {
      const response = await fetch("/api/branding", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      brandingStatus.textContent = "Saved.";
      setTimeout(closeBrandingModal, 500);
    } catch (err) {
      brandingStatus.textContent = `Could not save (${err.message || err}).`;
    }
  });

  // --- Live preview + subtitle editor --------------------------------

  let previewLoaded = false;

  function formatTimestamp(seconds) {
    const total = Math.max(0, Math.round(seconds));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  async function loadPreviewEditor() {
    if (previewLoaded) return; // video/track/transcript don't change while a job is open
    previewLoaded = true;

    previewVideo.querySelectorAll("track").forEach((el) => el.remove());
    previewVideo.src = `/api/jobs/${currentJobId}/video`;
    const track = document.createElement("track");
    track.kind = "subtitles";
    track.label = "Captions";
    track.srclang = "en";
    track.default = true;
    track.src = `/api/jobs/${currentJobId}/files/captions.vtt`;
    previewVideo.appendChild(track);

    editorStatus.textContent = "Loading transcript…";
    try {
      const response = await fetch(`/api/jobs/${currentJobId}/transcript`);
      const data = await response.json();
      renderCueList(data.segments || []);
      editorStatus.textContent = "";
    } catch (err) {
      editorStatus.textContent = `Could not load the transcript (${err.message || err}).`;
    }

    refreshDictionary();
  }

  function renderCueList(segments) {
    cueList.innerHTML = "";
    for (const segment of segments) {
      const li = document.createElement("li");
      li.className = "cue-row";

      const time = document.createElement("button");
      time.type = "button";
      time.className = "cue-time";
      time.textContent = formatTimestamp(segment.start);
      time.title = "Jump to this moment";
      time.setAttribute("aria-label", `Jump to ${formatTimestamp(segment.start)}`);
      time.addEventListener("click", () => {
        previewVideo.currentTime = segment.start;
        previewVideo.play();
      });

      const text = document.createElement("textarea");
      text.className = "cue-text";
      text.rows = 1;
      text.value = segment.text;
      text.dataset.start = segment.start;
      text.dataset.end = segment.end;
      text.setAttribute("aria-label", `Caption text at ${formatTimestamp(segment.start)}`);

      li.appendChild(time);
      li.appendChild(text);
      cueList.appendChild(li);
    }
  }

  function collectSegments() {
    return Array.from(cueList.querySelectorAll(".cue-text")).map((el) => ({
      start: Number(el.dataset.start),
      end: Number(el.dataset.end),
      text: el.value,
    }));
  }

  async function saveTranscript(statusVerb = "Saving…") {
    editorStatus.textContent = statusVerb;
    try {
      const response = await fetch(`/api/jobs/${currentJobId}/transcript`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: collectSegments() }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      // Captions were re-rendered server-side from the edited text; reload the
      // <track> so the browser picks up the new cues.
      const time = previewVideo.currentTime;
      previewVideo.querySelectorAll("track").forEach((el) => {
        el.src = `/api/jobs/${currentJobId}/files/captions.vtt?t=${Date.now()}`;
      });
      previewVideo.currentTime = time;
      editorStatus.textContent = "Saved.";
      setTimeout(() => (editorStatus.textContent = ""), 1500);
      return true;
    } catch (err) {
      editorStatus.textContent = `Could not save (${err.message || err}).`;
      return false;
    }
  }

  saveTranscriptBtn.addEventListener("click", () => saveTranscript());

  // --- Exact-word, case-insensitive find & replace for a single dictionary entry ---

  function wordRegex(word) {
    const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`\\b${escaped}\\b`, "gi");
  }

  function replacePreservingCase(match, replacement) {
    if (match.toUpperCase() === match && match.length > 1) return replacement.toUpperCase();
    if (match[0] === match[0].toUpperCase()) return replacement[0].toUpperCase() + replacement.slice(1);
    return replacement;
  }

  function countMatches(word) {
    const regex = wordRegex(word);
    let count = 0;
    for (const el of cueList.querySelectorAll(".cue-text")) {
      count += (el.value.match(regex) || []).length;
    }
    return count;
  }

  function replaceInCueList(wrong, right) {
    const regex = wordRegex(wrong);
    for (const el of cueList.querySelectorAll(".cue-text")) {
      el.value = el.value.replace(regex, (match) => replacePreservingCase(match, right));
    }
  }

  function offerReplacement(wrong, right) {
    const count = countMatches(wrong);
    if (!count || !cueList.children.length) {
      dictionaryOffer.hidden = true;
      return;
    }
    dictionaryOfferText.textContent = `Found ${count} match${count === 1 ? "" : "es"} of "${wrong}" in this video's subtitles — replace with "${right}"?`;
    dictionaryOffer.hidden = false;
    dictionaryOfferYes.onclick = async () => {
      replaceInCueList(wrong, right);
      dictionaryOffer.hidden = true;
      await saveTranscript("Replacing & saving…");
    };
    dictionaryOfferNo.onclick = () => {
      dictionaryOffer.hidden = true;
    };
  }

  applyDictBtn.addEventListener("click", async () => {
    editorStatus.textContent = "Applying dictionary…";
    try {
      const response = await fetch(`/api/jobs/${currentJobId}/transcript/apply-dictionary`, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      renderCueList(data.segments || []);
      const time = previewVideo.currentTime;
      previewVideo.querySelectorAll("track").forEach((el) => {
        el.src = `/api/jobs/${currentJobId}/files/captions.vtt?t=${Date.now()}`;
      });
      previewVideo.currentTime = time;
      editorStatus.textContent = "Dictionary applied and saved.";
      setTimeout(() => (editorStatus.textContent = ""), 1500);
    } catch (err) {
      editorStatus.textContent = `Could not apply dictionary (${err.message || err}).`;
    }
  });

  async function refreshDictionary() {
    try {
      const response = await fetch("/api/dictionary");
      const data = await response.json();
      renderDictionary(data.corrections || {});
    } catch {
      /* dictionary is optional; ignore load failures */
    }
  }

  function renderDictionary(corrections) {
    dictionaryList.innerHTML = "";
    const entries = Object.entries(corrections).sort(([a], [b]) => a.localeCompare(b));
    if (!entries.length) {
      const li = document.createElement("li");
      li.className = "dictionary-empty";
      li.textContent = "No custom spellings yet.";
      dictionaryList.appendChild(li);
      return;
    }
    for (const [wrong, right] of entries) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = `${wrong} → ${right}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost-btn small";
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", `Remove custom spelling "${wrong} → ${right}"`);
      remove.addEventListener("click", async () => {
        await fetch(`/api/dictionary/${encodeURIComponent(wrong)}`, { method: "DELETE" });
        refreshDictionary();
      });
      li.appendChild(label);
      li.appendChild(remove);
      dictionaryList.appendChild(li);
    }
  }

  dictionaryForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const wrong = dictWrong.value.trim();
    const right = dictRight.value.trim();
    if (!wrong || !right) return;
    const form = new FormData();
    form.append("wrong", wrong);
    form.append("right", right);
    try {
      const response = await fetch("/api/dictionary", { method: "POST", body: form });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      renderDictionary(data.corrections || {});
      dictionaryForm.reset();
      offerReplacement(wrong, right);
    } catch (err) {
      console.error("Failed to add dictionary entry", err);
    }
  });

  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(viewer.textContent);
      copyBtn.textContent = "Copied!";
      setTimeout(() => (copyBtn.textContent = "Copy"), 1200);
    } catch {
      /* clipboard API unavailable — ignore silently */
    }
  });

  for (const id of ["reset-btn", "reset-btn-2", "reset-btn-3"]) {
    document.getElementById(id).addEventListener("click", resetToUpload);
  }
})();
