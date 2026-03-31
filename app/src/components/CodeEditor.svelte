<script>
  import { onMount, onDestroy } from "svelte";
  import { readFile, writeFile, restoreFile, cleanupBackup, deleteFile, getCompletion, peekSymbol, getGitDiffLines, lintCode, fixLint } from "../lib/api.js";
  import * as monaco from "monaco-editor";
  import EditorTabBar from "./EditorTabBar.svelte";
  import DiffOverlay from "./DiffOverlay.svelte";

  let { activeFile = $bindable(""), openFiles = $bindable([]), workspacePath = "" } = $props();

  // Per-file state
  let filesState = $state({});

  // Pending AI diff: { path, stats: { added, changed, deleted } }
  let pendingDiff = $state(null);

  // Monaco instances
  let editorContainer;
  let editor = null;
  let models = {};                    // path → ITextModel
  let diffDecorations = null;         // IDecorationsCollection for AI diff highlights
  let writingDecorations = null;      // IDecorationsCollection for "AI writing" glow
  let gitGutterDecorations = null;    // IDecorationsCollection for git diff gutter bars
  let completionProviderDisposable = null;
  let codeActionProviderDisposable = null;
  let lintTimeout = null;

  // ── Language map ────────────────────────────────────────────────────────────
  const EXT_LANG = {
    js: "javascript", jsx: "javascript",
    ts: "typescript", tsx: "typescript",
    svelte: "html",
    py: "python",
    json: "json", jsonc: "json",
    css: "css", scss: "scss", less: "less",
    html: "html", htm: "html",
    md: "markdown",
    yaml: "yaml", yml: "yaml",
    toml: "plaintext",
    sh: "shell", bash: "shell",
    rs: "rust", go: "go",
    java: "java", c: "c", cpp: "cpp", h: "cpp",
    cs: "csharp", rb: "ruby", php: "php",
    sql: "sql", xml: "xml",
  };

  function detectLanguage(path) {
    const ext = path.split(".").pop()?.toLowerCase() ?? "";
    return EXT_LANG[ext] ?? "plaintext";
  }

  // ── GitHub Dark theme ───────────────────────────────────────────────────────
  function defineTheme() {
    monaco.editor.defineTheme("gitsurf-dark", {
      base: "vs-dark",
      inherit: true,
      rules: [
        { token: "comment",  foreground: "8b949e", fontStyle: "italic" },
        { token: "keyword",  foreground: "ff7b72" },
        { token: "string",   foreground: "a5d6ff" },
        { token: "number",   foreground: "79c0ff" },
        { token: "type",     foreground: "d2a8ff" },
        { token: "class",    foreground: "d2a8ff" },
        { token: "function", foreground: "d2a8ff" },
        { token: "variable", foreground: "ffa657" },
        { token: "operator", foreground: "ff7b72" },
      ],
      colors: {
        "editor.background":                  "#0d1117",
        "editor.foreground":                  "#c9d1d9",
        "editorLineNumber.foreground":        "#484f58",
        "editorLineNumber.activeForeground":  "#c9d1d9",
        "editor.selectionBackground":         "#264f78",
        "editor.inactiveSelectionBackground": "#1d3247",
        "editorCursor.foreground":            "#58a6ff",
        "editor.lineHighlightBackground":     "#161b22",
        "editorIndentGuide.background1":      "#21262d",
        "editorIndentGuide.activeBackground1":"#30363d",
        "scrollbarSlider.background":         "#30363d80",
        "scrollbarSlider.hoverBackground":    "#484f5880",
        "editorWidget.background":            "#161b22",
        "editorWidget.border":                "#30363d",
        "editorSuggestWidget.background":     "#161b22",
        "editorSuggestWidget.border":         "#30363d",
        "editorSuggestWidget.selectedBackground": "#21262d",
        // Diff colours (used by our decorations)
        "diffEditor.insertedLineBackground":  "#1b4332",
        "diffEditor.removedLineBackground":   "#4c1c1c",
      },
    });
  }

  // ── Simple line diff ────────────────────────────────────────────────────────
  function computeLineDiff(oldText, newText) {
    const oldLines = (oldText ?? "").split("\n");
    const newLines = (newText ?? "").split("\n");
    const added = [], changed = [];

    for (let i = 0; i < newLines.length; i++) {
      if (i >= oldLines.length) {
        added.push(i + 1);
      } else if (oldLines[i] !== newLines[i]) {
        changed.push(i + 1);
      }
    }
    const deleted = Math.max(0, oldLines.length - newLines.length);
    return { added, changed, deleted };
  }

  // ── onMount ─────────────────────────────────────────────────────────────────
  onMount(() => {
    defineTheme();

    editor = monaco.editor.create(editorContainer, {
      theme: "gitsurf-dark",
      automaticLayout: true,
      fontSize: 13,
      fontFamily: "var(--font-mono, 'JetBrains Mono', 'Fira Code', monospace)",
      fontLigatures: true,
      lineHeight: 20,
      minimap: { enabled: true, scale: 1 },
      scrollBeyondLastLine: false,
      wordWrap: "off",
      tabSize: 2,
      insertSpaces: true,
      renderWhitespace: "selection",
      bracketPairColorization: { enabled: true },
      guides: { bracketPairs: true, indentation: true },
      smoothScrolling: true,
      cursorBlinking: "smooth",
      cursorSmoothCaretAnimation: "on",
      padding: { top: 12, bottom: 12 },
      overviewRulerLanes: 0,
      hideCursorInOverviewRuler: true,
      scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
      // Inline suggestions (Copilot-style ghost text)
      inlineSuggest: { enabled: true },
    });

    // Ctrl+S → save
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      if (activeFile) handleSave(activeFile);
    });

    // F12 → Peek Definition (go to symbol definition)
    editor.addCommand(monaco.KeyCode.F12, async () => {
      const position = editor.getPosition();
      const model = editor.getModel();
      if (!position || !model) return;

      const word = model.getWordAtPosition(position);
      if (!word?.word) return;

      let res;
      try { res = await peekSymbol(word.word); } catch { return; }
      if (!res?.results?.length) {
        // Show a brief "not found" hint in the status area
        const msg = `No definition found for "${word.word}"`;
        editor.updateOptions({ ariaLabel: msg });
        setTimeout(() => editor.updateOptions({ ariaLabel: "Editor" }), 2000);
        return;
      }

      const first = res.results[0];
      // Absolute path: results from SymbolPeeker use relative paths
      const absPath = workspacePath
        ? workspacePath.replace(/\\/g, "/") + "/" + first.file.replace(/\\/g, "/")
        : first.file;

      if (activeFile === absPath || activeFile === first.file) {
        // Same file — just jump to the line
        editor.revealLineInCenter(first.start_line);
        editor.setPosition({ lineNumber: first.start_line, column: 1 });
      } else {
        // Different file — open it then navigate
        window.dispatchEvent(new CustomEvent("navigate-to-file-line", {
          detail: { path: absPath, line: first.start_line }
        }));
      }

      // If multiple results, log them to console for now (full peek widget later)
      if (res.results.length > 1) {
        console.info(`[F12] ${res.results.length} definitions found for "${word.word}":`,
          res.results.map(r => `${r.file}:${r.start_line}`));
      }
    });

    // ── AI Context Menu Actions ────────────────────────────────────────────────
    const AI_CONTEXT_ACTIONS = [
      { id: "gitsurf.explain",  label: "⚡ Explain Selection",  autoSend: true  },
      { id: "gitsurf.refactor", label: "🔧 Refactor Selection", autoSend: false },
      { id: "gitsurf.fix",      label: "🐛 Fix Bug",            autoSend: false },
      { id: "gitsurf.tests",    label: "🧪 Add Tests",          autoSend: false },
      { id: "gitsurf.docs",     label: "📝 Add Docs",           autoSend: false },
    ];

    AI_CONTEXT_ACTIONS.forEach((action, i) => {
      editor.addAction({
        id: action.id,
        label: action.label,
        contextMenuGroupId: "z_gitsurf",
        contextMenuOrder: i,
        run(ed) {
          const selection = ed.getSelection();
          const model = ed.getModel();
          if (!selection || !model) return;
          const selectedText = model.getValueInRange(selection);
          if (!selectedText.trim()) return;

          const fileName = activeFile ? activeFile.split(/[/\\]/).pop() : "file";
          const lang = detectLanguage(activeFile ?? "");
          const codeBlock = `\`\`\`${lang}\n${selectedText}\n\`\`\``;

          const queryMap = {
            "gitsurf.explain":  `Explain this code from ${fileName}:\n\n${codeBlock}`,
            "gitsurf.refactor": `Refactor this code from ${fileName}:\n\n${codeBlock}`,
            "gitsurf.fix":      `Fix the bug in this code from ${fileName}:\n\n${codeBlock}`,
            "gitsurf.tests":    `Write unit tests for this code from ${fileName}:\n\n${codeBlock}`,
            "gitsurf.docs":     `Add documentation comments to this code from ${fileName}:\n\n${codeBlock}`,
          };

          window.dispatchEvent(new CustomEvent("chat-prefill", {
            detail: { query: queryMap[action.id], autoSend: action.autoSend }
          }));
        },
      });
    });

    // ── Feature 4: Inline completions provider ────────────────────────────────
    completionProviderDisposable = monaco.languages.registerInlineCompletionsProvider("*", {
      provideInlineCompletions: async (model, position, _context, token) => {
        // Only trigger if engine is available and file is loaded
        const path = activeFile;
        if (!path || !filesState[path] || filesState[path].isLoading) return { items: [] };

        const offset   = model.getOffsetAt(position);
        const fullText = model.getValue();
        const prefix   = fullText.substring(0, offset);
        const suffix   = fullText.substring(offset);

        // Don't trigger for very short prefixes
        if (prefix.trimEnd().length < 15) return { items: [] };

        try {
          const result = await getCompletion(
            path,
            prefix.slice(-900),
            suffix.slice(0, 200),
            detectLanguage(path)
          );
          if (token.isCancellationRequested || !result?.completion) return { items: [] };

          return {
            items: [{
              insertText: result.completion,
              range: new monaco.Range(
                position.lineNumber, position.column,
                position.lineNumber, position.column
              ),
            }],
          };
        } catch {
          return { items: [] };
        }
      },
      freeInlineCompletions: () => {},
    });

    // ── Feature 5: "Fix with AI" code action ────────────────────────────────
    codeActionProviderDisposable = monaco.languages.registerCodeActionProvider("*", {
      provideCodeActions(model, range, context) {
        const markers = context.markers.filter(m => m.source === "gitsurf-lint");
        if (markers.length === 0) return { actions: [], dispose() {} };

        const action = {
          title: "⚡ Fix with AI",
          kind: "quickfix",
          diagnostics: markers,
          isPreferred: true,
        };
        action.command = {
          id: "gitsurf.fixLintAI",
          title: "Fix with AI",
          arguments: [model.uri.toString(), markers],
        };
        return { actions: [action], dispose() {} };
      },
    });

    editor.addCommand(0, async (_accessor, uri, markers) => {
      const path = activeFile;
      if (!path || !models[path]) return;

      const content = models[path].getValue();
      const diagnostics = markers.map(m => ({
        line: m.startLineNumber,
        column: m.startColumn,
        message: m.message,
        severity: m.severity === monaco.MarkerSeverity.Error ? "error" : "warning",
        source: m.source || "lint",
        code: "",
      }));

      try {
        const { fixed_content } = await fixLint(path, content, diagnostics);
        if (fixed_content && fixed_content !== content) {
          models[path].pushEditOperations([], [{
            range: models[path].getFullModelRange(),
            text: fixed_content,
          }], () => null);
        }
      } catch (e) {
        console.error("Fix with AI failed:", e.message);
      }
    }, "gitsurf.fixLintAI");

    // ── Feature 3: AI writing decoration ─────────────────────────────────────
    const handleWritingStart = (e) => {
      const { path } = e.detail;
      if (!editor) return;
      const model = path === activeFile ? editor.getModel() : null;
      if (!model) return;

      writingDecorations?.clear();
      const lineCount = model.getLineCount();
      const decorations = Array.from({ length: lineCount }, (_, i) => ({
        range: new monaco.Range(i + 1, 1, i + 1, 1),
        options: {
          isWholeLine: true,
          className: "ai-writing-glow",
          overviewRuler: { color: "#58a6ff", position: monaco.editor.OverviewRulerLane.Full },
        },
      }));
      writingDecorations = editor.createDecorationsCollection(decorations);
    };

    // ── Feature 1 & 2: AI diff apply + Accept/Reject ─────────────────────────
    const handleFileChanged = async (e) => {
      const { path } = e.detail;
      if (!editor) return;

      // Clear writing glow
      writingDecorations?.clear();
      writingDecorations = null;

      // Get the current model content (before AI write)
      const currentContent = models[path]
        ? models[path].getValue()
        : (filesState[path]?.original ?? "");

      // Fetch new content from disk
      let newContent;
      try {
        const res = await readFile(path);
        newContent = res.content;
      } catch {
        return;
      }

      // Update model with new content
      if (models[path]) {
        models[path].pushEditOperations([], [{
          range: models[path].getFullModelRange(),
          text: newContent,
        }], () => null);
      } else {
        // Model not yet created — store for when it mounts
        if (filesState[path]) filesState[path].content = newContent;
      }

      // Compute diff and build decorations
      const diff = computeLineDiff(currentContent, newContent);
      const decorations = [];

      diff.added.forEach(line => {
        decorations.push({
          range: new monaco.Range(line, 1, line, 1),
          options: {
            isWholeLine: true,
            className: "ai-diff-added",
            glyphMarginClassName: "ai-diff-glyph-added",
            overviewRuler: { color: "#3fb950", position: monaco.editor.OverviewRulerLane.Left },
          },
        });
      });

      diff.changed.forEach(line => {
        decorations.push({
          range: new monaco.Range(line, 1, line, 1),
          options: {
            isWholeLine: true,
            className: "ai-diff-changed",
            glyphMarginClassName: "ai-diff-glyph-changed",
            overviewRuler: { color: "#e3b341", position: monaco.editor.OverviewRulerLane.Left },
          },
        });
      });

      if (models[path]) {
        diffDecorations?.clear();
        diffDecorations = editor.createDecorationsCollection(decorations);
      }

      // Update state
      if (filesState[path]) {
        filesState[path].isDirty = false;
        filesState[path].original = newContent;
      }

      pendingDiff = {
        path,
        stats: {
          added: diff.added.length,
          changed: diff.changed.length,
          deleted: diff.deleted,
        },
      };

      // Scroll to first changed line
      const firstLine = diff.added[0] ?? diff.changed[0];
      if (firstLine) editor.revealLineInCenter(firstLine);
    };

    // ── Feature: AI new file created ─────────────────────────────────────────
    const handleFileCreated = async (e) => {
      const { path } = e.detail;
      if (!editor) return;

      // Clear writing glow
      writingDecorations?.clear();
      writingDecorations = null;

      // Load the new file content
      let newContent = "";
      try {
        const res = await readFile(path);
        newContent = res.content;
      } catch {
        return;
      }

      // Initialize filesState for this new file (no original — it's brand new)
      filesState[path] = { content: newContent, original: null, isLoading: false, isDirty: false, isSaving: false, saveStatus: "", error: "" };

      // Build and swap model
      if (models[path]) { models[path].dispose(); delete models[path]; }
      swapModel(path);

      // Mark all lines as added
      const lineCount = newContent.split("\n").length;
      const decorations = Array.from({ length: lineCount }, (_, i) => ({
        range: new monaco.Range(i + 1, 1, i + 1, 1),
        options: {
          isWholeLine: true,
          className: "ai-diff-added",
          glyphMarginClassName: "ai-diff-glyph-added",
          overviewRuler: { color: "#3fb950", position: monaco.editor.OverviewRulerLane.Left },
        },
      }));

      // Wait a tick so swapModel has set the model
      setTimeout(() => {
        if (models[path]) {
          diffDecorations?.clear();
          diffDecorations = editor.createDecorationsCollection(decorations);
        }
      }, 50);

      pendingDiff = {
        path,
        isNewFile: true,
        stats: { added: lineCount, changed: 0, deleted: 0 },
      };
    };

    // ── navigate-to-line ──────────────────────────────────────────────────────
    const handleNavigate = (e) => {
      const { path, line } = e.detail;
      if (path === activeFile && editor) {
        editor.revealLineInCenter(line);
        editor.setPosition({ lineNumber: line, column: 1 });
        editor.focus();
      }
    };

    // ── branch-changed ────────────────────────────────────────────────────────
    const handleBranchChange = () => {
      Object.values(models).forEach(m => m.dispose());
      models = {};
      filesState = {};
      pendingDiff = null;
      if (activeFile) loadContent(activeFile);
    };

    window.addEventListener("ai-writing-start",  handleWritingStart);
    window.addEventListener("ai-file-changed",   handleFileChanged);
    window.addEventListener("ai-file-created",   handleFileCreated);
    window.addEventListener("navigate-to-line",  handleNavigate);
    window.addEventListener("branch-changed",    handleBranchChange);

    return () => {
      window.removeEventListener("ai-writing-start",  handleWritingStart);
      window.removeEventListener("ai-file-changed",   handleFileChanged);
      window.removeEventListener("ai-file-created",   handleFileCreated);
      window.removeEventListener("navigate-to-line",  handleNavigate);
      window.removeEventListener("branch-changed",    handleBranchChange);
    };
  });

  onDestroy(() => {
    clearTimeout(lintTimeout);
    completionProviderDisposable?.dispose();
    codeActionProviderDisposable?.dispose();
    Object.values(models).forEach(m => m.dispose());
    editor?.dispose();
  });

  // ── Reactive: swap model when activeFile changes ─────────────────────────────
  $effect(() => {
    if (!editor) return;
    if (!activeFile) {
      editor.setModel(null);
      return;
    }
    if (!filesState[activeFile]) {
      loadContent(activeFile);
      return;
    }
    if (!filesState[activeFile].isLoading) {
      swapModel(activeFile);
      applyGitGutter(activeFile);
    }
  });

  function swapModel(path) {
    if (!editor || !filesState[path] || filesState[path].isLoading) return;

    if (!models[path]) {
      const uri = monaco.Uri.parse(`file:///${path.replace(/\\/g, "/")}`);
      models[path] = monaco.editor.createModel(
        filesState[path].content,
        detectLanguage(path),
        uri
      );
      models[path].onDidChangeContent(() => {
        if (filesState[path]) {
          filesState[path].isDirty = models[path].getValue() !== filesState[path].original;
        }
        // Debounced real-time lint
        clearTimeout(lintTimeout);
        lintTimeout = setTimeout(() => lintCurrentFile(path), 500);
      });
    }

    editor.setModel(models[path]);

    // Re-apply diff decorations if they belong to this file
    if (pendingDiff?.path === path && diffDecorations) {
      // decorations are already on the collection, Monaco retains them
    }

    editor.focus();
  }

  // ── Load from backend ────────────────────────────────────────────────────────
  async function loadContent(path) {
    filesState[path] = { content: "", original: "", isLoading: true, isDirty: false, isSaving: false, saveStatus: "", error: "" };

    try {
      const res = await readFile(path);
      filesState[path].content  = res.content;
      filesState[path].original = res.content;

      if (models[path]) {
        models[path].dispose();
        delete models[path];
      }
    } catch (e) {
      filesState[path].error = "Failed to load: " + e.message;
    } finally {
      filesState[path].isLoading = false;
      swapModel(path);
    }
  }

  // ── Real-time lint ───────────────────────────────────────────────────────────
  const LINTABLE_EXTS = new Set(["py", "js", "ts", "jsx", "tsx"]);

  async function lintCurrentFile(path) {
    if (!path || !models[path] || !workspacePath) return;
    const ext = path.split(".").pop()?.toLowerCase() ?? "";
    if (!LINTABLE_EXTS.has(ext)) return;
    const content = models[path].getValue();
    try {
      const { diagnostics } = await lintCode(path, content, workspacePath);
      const markers = (diagnostics ?? []).map((d) => ({
        severity: d.severity === "error" ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
        startLineNumber: d.line,
        startColumn: d.column,
        endLineNumber: d.end_line ?? d.line,
        endColumn: d.end_column ?? d.column + 1,
        message: `[${d.source}] ${d.message}${d.code ? ` (${d.code})` : ""}`,
        source: d.source,
      }));
      monaco.editor.setModelMarkers(models[path], "gitsurf-lint", markers);
    } catch {}
  }

  // ── Save ─────────────────────────────────────────────────────────────────────
  async function handleSave(path) {
    const f = filesState[path];
    if (!f || f.isSaving || !f.isDirty) return;

    const content = models[path]?.getValue() ?? f.content;
    f.isSaving   = true;
    f.saveStatus = "saving";

    try {
      await writeFile(path, content);
      f.original   = content;
      f.isDirty    = false;
      f.saveStatus = "success";
      setTimeout(() => { if (f.saveStatus === "success") f.saveStatus = ""; }, 2000);
      applyGitGutter(path);
    } catch (e) {
      f.error      = "Failed to save: " + e.message;
      f.saveStatus = "error";
    } finally {
      f.isSaving = false;
    }
  }

  // ── Git gutter diff ──────────────────────────────────────────────────────────
  async function applyGitGutter(path) {
    if (!editor || !path || !workspacePath) return;
    try {
      const { added, modified } = await getGitDiffLines(path);
      const decorations = [];
      for (const line of added) {
        decorations.push({
          range: new monaco.Range(line, 1, line, 1),
          options: {
            linesDecorationsClassName: "git-gutter-added-bar",
            overviewRuler: { color: "#3fb950", position: monaco.editor.OverviewRulerLane.Right },
          },
        });
      }
      for (const line of modified) {
        decorations.push({
          range: new monaco.Range(line, 1, line, 1),
          options: {
            linesDecorationsClassName: "git-gutter-modified-bar",
            overviewRuler: { color: "#58a6ff", position: monaco.editor.OverviewRulerLane.Right },
          },
        });
      }
      gitGutterDecorations?.clear();
      gitGutterDecorations = editor.createDecorationsCollection(decorations);
    } catch {
      // silently fail — gutter is optional
    }
  }

  // ── Accept AI diff ────────────────────────────────────────────────────────────
  async function acceptDiff() {
    if (!pendingDiff) return;
    const { path, isNewFile } = pendingDiff;

    // Finalize: sync original to current model content
    if (filesState[path] && models[path]) {
      const currentValue = models[path].getValue();
      filesState[path].content  = currentValue;
      filesState[path].original = currentValue;
      filesState[path].isDirty  = false;
    }

    // Clean up .bak file only for edits (new files have no .bak)
    if (!isNewFile) {
      try { await cleanupBackup(path); } catch {}
    }

    diffDecorations?.clear();
    diffDecorations = null;
    pendingDiff = null;
    applyGitGutter(path);
  }

  // ── Reject AI diff ────────────────────────────────────────────────────────────
  async function rejectDiff() {
    if (!pendingDiff) return;
    const { path, isNewFile } = pendingDiff;

    if (isNewFile) {
      // Delete the newly created file and close its tab
      try { await deleteFile(path); } catch (e) { console.error("Delete failed:", e.message); }
      models[path]?.dispose();
      delete models[path];
      delete filesState[path];
      openFiles = openFiles.filter(p => p !== path);
      if (activeFile === path) {
        activeFile = openFiles.length > 0 ? openFiles[openFiles.length - 1] : "";
      }
    } else {
      try {
        await restoreFile(path);
        // Reload model from restored content
        const res = await readFile(path);
        if (models[path]) {
          models[path].pushEditOperations([], [{
            range: models[path].getFullModelRange(),
            text: res.content,
          }], () => null);
        }
        if (filesState[path]) {
          filesState[path].content  = res.content;
          filesState[path].original = res.content;
          filesState[path].isDirty  = false;
        }
        // Clean up .bak file after restore
        try { await cleanupBackup(path); } catch {}
      } catch (e) {
        console.error("Reject failed:", e.message);
      }
    }

    diffDecorations?.clear();
    diffDecorations = null;
    pendingDiff = null;
  }

  // ── Close tab ────────────────────────────────────────────────────────────────
  function handleCloseTab(path) {
    const index = openFiles.indexOf(path);
    if (index === -1) return;

    models[path]?.dispose();
    delete models[path];
    delete filesState[path];

    if (pendingDiff?.path === path) {
      diffDecorations?.clear();
      pendingDiff = null;
    }

    openFiles = openFiles.filter(p => p !== path);
    if (activeFile === path) {
      activeFile = openFiles.length > 0
        ? openFiles[Math.min(index, openFiles.length - 1)]
        : "";
    }
  }
</script>

<div class="editor">
  <EditorTabBar
    {openFiles}
    bind:activeFile
    {filesState}
    {pendingDiff}
    onclose={handleCloseTab}
    onsave={handleSave}
  />

  <DiffOverlay
    {pendingDiff}
    {activeFile}
    onaccept={acceptDiff}
    onreject={rejectDiff}
  />

  <!-- Editor body -->
  <div class="editor__body">
    {#if !activeFile}
      <div class="editor__status welcome">
        <div class="welcome-icon">🌊</div>
        <h3>Ready to surf?</h3>
        <p>Select a file from the explorer to start editing.</p>
        <p class="editor__hint">Tab accepts inline AI suggestions</p>
      </div>
    {:else if filesState[activeFile]?.isLoading}
      <div class="editor__status">
        <div class="spinner"></div>
        Loading...
      </div>
    {:else if filesState[activeFile]?.error}
      <div class="editor__status editor__status--error">
        <span class="error-icon">⚠️</span>
        {filesState[activeFile].error}
        <button onclick={() => loadContent(activeFile)}>Retry</button>
      </div>
    {/if}

    <div
      bind:this={editorContainer}
      class="editor__monaco"
      class:editor__monaco--hidden={!activeFile || filesState[activeFile]?.isLoading || filesState[activeFile]?.error}
    ></div>
  </div>
</div>

<style>
  .editor {
    height: 100%;
    display: flex;
    flex-direction: column;
    background: var(--bg-primary);
    color: var(--text-primary);
  }

  /* ── Editor body ── */
  .editor__body {
    flex: 1; position: relative; overflow: hidden;
  }
  .editor__monaco {
    position: absolute; inset: 0;
  }
  .editor__monaco--hidden {
    visibility: hidden; pointer-events: none;
  }

  /* ── Status screens ── */
  .editor__status {
    position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 12px;
    color: var(--text-muted); font-size: 14px; text-align: center;
    z-index: 1; background: var(--bg-primary);
  }
  .editor__status.welcome { opacity: 0.7; }
  .welcome-icon { font-size: 48px; margin-bottom: 8px; }
  .editor__status h3 { color: var(--text-primary); margin: 0; font-size: 15px; }
  .editor__status p  { margin: 0; font-size: 13px; }
  .editor__hint { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
  .editor__status--error { color: var(--accent-red); padding: 20px; }
  .error-icon { font-size: 24px; }
  .spinner {
    width: 20px; height: 20px;
    border: 2px solid rgba(255,255,255,0.1);
    border-top-color: var(--accent-blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
