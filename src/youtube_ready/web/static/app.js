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

    let models = [];
    let error = null;
    try {
      const response = await fetch(`/api/models?${new URLSearchParams({ backend })}`);
      const data = await response.json();
      models = data.models || [];
      error = data.error || null;
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
    }
  }

  modelSelect.addEventListener("change", () => {
    modelSelect.title = modelSelect.value;
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
  const viewer = document.getElementById("viewer");
  const copyBtn = document.getElementById("copy-btn");
  const downloadBtn = document.getElementById("download-btn");
  const resultsPath = document.getElementById("results-path");
  const tabToolbar = document.querySelector(".tab-toolbar");

  const thumbnailsToggle = document.getElementById("thumbnails");
  const thumbnailStudio = document.getElementById("thumbnail-studio");
  const studioSwitcher = document.getElementById("studio-switcher");
  const studioEditor = document.getElementById("studio-editor");
  const studioProvenance = document.getElementById("studio-provenance");
  const studioDownloadAll = document.getElementById("studio-download-all");

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
    form.append("thumbnails", thumbnailsToggle.checked);

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
      } else if (payload.type === "done") {
        source.close();
        showResults(payload.output_dir, payload.files, payload.thumbnails, payload.thumbnail_variants);
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
          if (data.status === "done") showResults(data.output_dir, data.files, data.thumbnails, data.thumbnail_variants);
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
      reopenBtn.addEventListener("click", () => reopenSession(session.name));

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "ghost-btn small danger";
      deleteBtn.textContent = "Delete";
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
      showResults(data.output_dir, data.files, data.thumbnails, data.thumbnail_variants);
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

  function showResults(outputDir, files, thumbnails, thumbnailVariants) {
    resultsPath.textContent = outputDir;
    currentThumbnails = thumbnails || null;
    currentThumbnailVariants = thumbnailVariants || null;
    tabsEl.innerHTML = "";
    const visible = files.filter((f) => !HIDDEN_FROM_TABS.has(f));
    const previewable = visible.filter((f) => PREVIEWABLE.has(f));
    const rest = visible.filter((f) => !PREVIEWABLE.has(f));
    const hasThumbnailTab = currentThumbnailVariants && currentThumbnailVariants.variants && currentThumbnailVariants.variants.length;
    const ordered = [PREVIEW_TAB, ...(hasThumbnailTab ? [THUMBNAILS_TAB] : []), ...previewable, ...rest];

    ordered.forEach((name, index) => {
      const tab = document.createElement("button");
      tab.className = "tab";
      tab.textContent = name;
      tab.addEventListener("click", () => selectTab(name));
      tabsEl.appendChild(tab);
      if (index === 0) selectTab(name);
    });

    showPanel(resultsPanel);
  }

  async function selectTab(name) {
    currentTab = name;
    for (const tab of tabsEl.querySelectorAll(".tab")) {
      tab.classList.toggle("active", tab.textContent === name);
    }

    const isPreview = name === PREVIEW_TAB;
    const isThumbnails = name === THUMBNAILS_TAB;
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

  const STRATEGY_LABELS = { search: "Clarity / Search", browse: "Curiosity / Browse", outcome: "Outcome / Stakes" };
  const studioZoomState = {}; // variant_id -> last zoom value, so focal clicks reuse it

  function thumbUrl(filename, bust) {
    if (!filename) return "";
    const url = `/api/jobs/${currentJobId}/thumbnails/${encodeURIComponent(filename)}`;
    return bust ? `${url}?t=${Date.now()}` : url;
  }

  // Which of the 3 variants (search/browse/outcome) the single big editor is
  // currently showing. Persists across re-renders so an edit doesn't bounce you
  // back to the first tab.
  let activeStudioVariantId = null;

  function renderThumbnailStudio() {
    studioSwitcher.innerHTML = "";
    studioEditor.innerHTML = "";
    if (!currentThumbnailVariants || !currentThumbnailVariants.variants || !currentThumbnailVariants.variants.length) {
      studioEditor.textContent = "No thumbnail packages were generated for this video.";
      studioProvenance.textContent = "";
      studioDownloadAll.hidden = true;
      return;
    }
    const pkg = currentThumbnailVariants;
    studioProvenance.textContent = pkg.fallback_reason
      ? `Heuristic fallback used: ${pkg.fallback_reason}`
      : `Generated with ${pkg.source}${pkg.model ? ` (${pkg.model})` : ""}`;
    studioDownloadAll.hidden = false;
    studioDownloadAll.href = `/api/jobs/${currentJobId}/thumbnail-variants/download-all`;
    studioDownloadAll.setAttribute("download", "thumbnails.zip");

    if (!pkg.variants.some((v) => v.variant_id === activeStudioVariantId)) {
      activeStudioVariantId = pkg.variants[0].variant_id;
    }

    for (const variant of pkg.variants) {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "studio-switch-btn";
      tab.classList.toggle("active", variant.variant_id === activeStudioVariantId);
      const label = document.createElement("span");
      label.textContent = STRATEGY_LABELS[variant.strategy] || variant.strategy;
      tab.appendChild(label);
      if (variant.warnings && variant.warnings.length) {
        const dot = document.createElement("span");
        dot.className = "switch-warning-dot";
        dot.title = `${variant.warnings.length} warning${variant.warnings.length === 1 ? "" : "s"}`;
        tab.appendChild(dot);
      }
      tab.addEventListener("click", () => {
        activeStudioVariantId = variant.variant_id;
        renderThumbnailStudio();
      });
      studioSwitcher.appendChild(tab);
    }

    const activeVariant = pkg.variants.find((v) => v.variant_id === activeStudioVariantId);
    studioEditor.dataset.variantId = activeVariant.variant_id;
    studioEditor.appendChild(buildStudioEditor(activeVariant, pkg.frames || []));
  }

  function buildStudioEditor(variant, frames) {
    // No wrapper div here: #studio-editor itself is the CSS grid (2 columns), so
    // `visual` and `fields` must be appended directly as its children — wrapping
    // them in one extra div made them a single grid item, collapsing both columns
    // into the first cell and leaving the second column empty.
    const editor = document.createDocumentFragment();

    const visual = document.createElement("div");
    visual.className = "studio-editor-visual";

    const provenance = document.createElement("div");
    provenance.className = "studio-editor-provenance";
    provenance.textContent = `${variant.source}${variant.fallback_reason ? " (fallback)" : ""}${variant.model ? ` · ${variant.model}` : ""}`;
    visual.appendChild(provenance);

    const preview = document.createElement("div");
    preview.className = "studio-preview";
    preview.tabIndex = 0;
    preview.setAttribute("role", "button");
    preview.setAttribute(
      "aria-label",
      "Thumbnail crop preview. Click a spot to recenter on it, or use the arrow keys."
    );
    const img = document.createElement("img");
    // Rendered files are always written to the same name per strategy (variant-search.jpg
    // etc.), so the URL never changes across edits — bust the cache or the browser just
    // keeps showing the image from before the edit.
    img.src = thumbUrl(variant.rendered_filename, true) || (variant.frame_filename ? thumbUrl(variant.frame_filename, true) : "");
    img.alt = variant.title;
    preview.appendChild(img);
    const focalHint = document.createElement("span");
    focalHint.className = "hint-overlay";
    focalHint.textContent = "Click to re-center";
    preview.appendChild(focalHint);
    preview.addEventListener("click", async (event) => {
      const rect = img.getBoundingClientRect();
      const focalX = (event.clientX - rect.left) / rect.width;
      const focalY = (event.clientY - rect.top) / rect.height;
      // At 100% zoom the crop already fills the frame, so re-centering has no visible
      // effect — nudge in slightly the first time so a click always does something.
      const zoom = studioZoomState[variant.variant_id] ?? 0.85;
      studioZoomState[variant.variant_id] = zoom;
      zoomInput.value = String(zoom);
      await saveVariantEdit(
        variant.variant_id,
        {
          focal_x: Math.min(Math.max(focalX, 0), 1),
          focal_y: Math.min(Math.max(focalY, 0), 1),
          zoom,
        },
        status
      );
    });
    preview.addEventListener("keydown", async (event) => {
      const step = 0.05;
      let dx = 0;
      let dy = 0;
      if (event.key === "ArrowLeft") dx = -step;
      else if (event.key === "ArrowRight") dx = step;
      else if (event.key === "ArrowUp") dy = -step;
      else if (event.key === "ArrowDown") dy = step;
      else return;
      event.preventDefault();
      const zoom = studioZoomState[variant.variant_id] ?? variant.zoom ?? 0.85;
      studioZoomState[variant.variant_id] = zoom;
      zoomInput.value = String(zoom);
      await saveVariantEdit(
        variant.variant_id,
        {
          focal_x: Math.min(Math.max((variant.focal_x ?? 0.5) + dx, 0), 1),
          focal_y: Math.min(Math.max((variant.focal_y ?? 0.5) + dy, 0), 1),
          zoom,
        },
        status
      );
    });
    visual.appendChild(preview);

    const zoomRow = document.createElement("div");
    zoomRow.className = "studio-zoom";
    const zoomLabel = document.createElement("span");
    zoomLabel.textContent = "Zoom";
    const zoomInput = document.createElement("input");
    zoomInput.type = "range";
    zoomInput.min = "0.3";
    zoomInput.max = "1";
    zoomInput.step = "0.05";
    zoomInput.value = String(studioZoomState[variant.variant_id] ?? 1);
    zoomInput.title = "Zoom in, then click the image to re-center on a different spot";
    zoomInput.addEventListener("change", async () => {
      studioZoomState[variant.variant_id] = Number(zoomInput.value);
      await saveVariantEdit(
        variant.variant_id,
        { focal_x: variant.focal_x, focal_y: variant.focal_y, zoom: Number(zoomInput.value) },
        status
      );
    });
    zoomRow.appendChild(zoomLabel);
    zoomRow.appendChild(zoomInput);
    visual.appendChild(zoomRow);

    if (frames.length) {
      const stripHint = document.createElement("span");
      stripHint.className = "hint";
      stripHint.textContent = "Click a candidate frame to use it for this package";
      visual.appendChild(stripHint);
      const strip = document.createElement("div");
      strip.className = "studio-frame-strip";
      for (const frame of frames) {
        if (frame.is_duplicate || !frame.filename) continue;
        const thumb = document.createElement("img");
        thumb.src = thumbUrl(frame.preview_filename || frame.filename);
        thumb.alt = `Candidate frame at ${frame.timestamp.toFixed(1)}s`;
        thumb.title = `${frame.timestamp.toFixed(1)}s`;
        if (variant.frame_filename === frame.filename) thumb.classList.add("active");
        thumb.addEventListener("click", async () => {
          await saveVariantEdit(variant.variant_id, { frame_filename: frame.filename });
        });
        strip.appendChild(thumb);
      }
      visual.appendChild(strip);
    }

    editor.appendChild(visual);

    const fields = document.createElement("div");
    fields.className = "studio-editor-fields";

    // A single status line, shared by every control on this card, sits right under
    // the text fields — every edit here (typing, clicking the image, dragging zoom,
    // regenerating, uploading) auto-applies and re-renders the same way, so there's
    // one consistent "did my change take?" signal instead of a separate save step.
    const status = document.createElement("span");
    status.className = "hint studio-status";
    status.setAttribute("aria-live", "polite");

    const titleField = document.createElement("div");
    titleField.className = "studio-field";
    titleField.innerHTML = `<label>Title <span class="hint">saves when you click away or press Enter</span></label>`;
    const titleInput = document.createElement("input");
    titleInput.type = "text";
    titleInput.value = variant.title;
    titleField.appendChild(titleInput);
    fields.appendChild(titleField);

    const overlayField = document.createElement("div");
    overlayField.className = "studio-field";
    overlayField.innerHTML = `<label>Overlay text <span class="hint">saves when you click away or press Enter</span></label>`;
    const overlayInput = document.createElement("input");
    overlayInput.type = "text";
    overlayInput.value = variant.overlay_text;
    overlayField.appendChild(overlayInput);
    fields.appendChild(overlayField);

    function saveText() {
      if (titleInput.value === variant.title && overlayInput.value === variant.overlay_text) return;
      saveVariantEdit(variant.variant_id, { title: titleInput.value, overlay_text: overlayInput.value }, status);
    }
    for (const input of [titleInput, overlayInput]) {
      input.addEventListener("change", saveText); // fires on blur, only if the value changed
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") input.blur();
      });
    }

    fields.appendChild(status);

    if (variant.visual_rationale) {
      const rationale = document.createElement("p");
      rationale.className = "hint";
      rationale.textContent = variant.visual_rationale;
      fields.appendChild(rationale);
    }

    if (variant.warnings && variant.warnings.length) {
      const warnings = document.createElement("ul");
      warnings.className = "studio-warnings";
      for (const warning of variant.warnings) {
        const li = document.createElement("li");
        li.textContent = warning;
        warnings.appendChild(li);
      }
      fields.appendChild(warnings);
    }

    const actions = document.createElement("div");
    actions.className = "studio-actions";

    const regenBtn = document.createElement("button");
    regenBtn.className = "ghost-btn small";
    regenBtn.textContent = "Regenerate with AI";
    regenBtn.title = "Ask the model for a new overlay and frame for this package";
    regenBtn.addEventListener("click", () => regenerateVariant(variant.variant_id, status));
    actions.appendChild(regenBtn);

    const uploadLabel = document.createElement("label");
    uploadLabel.className = "ghost-btn small";
    uploadLabel.textContent = "Use my own photo";
    const uploadInput = document.createElement("input");
    uploadInput.type = "file";
    uploadInput.accept = "image/*";
    uploadInput.hidden = true;
    uploadInput.addEventListener("change", () => {
      if (uploadInput.files?.[0]) uploadVariantImage(variant.variant_id, uploadInput.files[0], status);
    });
    uploadLabel.appendChild(uploadInput);
    actions.appendChild(uploadLabel);

    const downloadLink = document.createElement("a");
    downloadLink.className = "ghost-btn small";
    downloadLink.textContent = "Download this image";
    downloadLink.href = thumbUrl(variant.rendered_filename);
    downloadLink.setAttribute("download", `variant-${variant.strategy}.jpg`);
    actions.appendChild(downloadLink);

    const selectBtn = document.createElement("button");
    selectBtn.className = "ghost-btn small";
    selectBtn.textContent = "I'm using this one";
    selectBtn.title = "Record which package you actually published, and optionally a real YouTube test result";
    selectBtn.addEventListener("click", () => selectVariantForTest(variant.variant_id, status));
    actions.appendChild(selectBtn);

    fields.appendChild(actions);
    editor.appendChild(fields);
    return editor;
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
    status.textContent = "Regenerating…";
    try {
      const response = await fetch(
        `/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/regenerate`,
        { method: "POST" }
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Regenerated.");
    } catch (err) {
      status.textContent = `Could not regenerate (${err.message || err}).`;
    }
  }

  async function uploadVariantImage(variantId, file, status) {
    status.textContent = "Uploading…";
    const form = new FormData();
    form.append("image", file);
    try {
      const response = await fetch(
        `/api/jobs/${currentJobId}/thumbnail-variants/${encodeURIComponent(variantId)}/upload`,
        { method: "POST", body: form }
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      currentThumbnailVariants = await response.json();
      renderThumbnailStudio();
      flashStatus(variantId, "Photo uploaded.");
    } catch (err) {
      status.textContent = `Could not upload (${err.message || err}).`;
    }
  }

  async function selectVariantForTest(variantId, status) {
    const result = await openConfirmDialog({
      title: "Record this package as the one you're using",
      message: "Optional: paste a real YouTube A/B test result or note.",
      confirmLabel: "Record",
      showInput: true,
      inputLabel: "Result or note",
      placeholder: "Leave blank to just record the selection",
    });
    if (result === null) return;
    status.textContent = "Recording…";
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
      status.textContent = "Recorded.";
      setTimeout(() => (status.textContent = ""), 1500);
    } catch (err) {
      status.textContent = `Could not record (${err.message || err}).`;
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
