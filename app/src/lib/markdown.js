/**
 * Markdown renderer using marked v15 + highlight.js
 * Used exclusively for assistant messages in ChatPanel.
 */

import { marked, Renderer } from "marked";
import hljs from "highlight.js";

// ── File path detection ───────────────────────────────────────────────────────

const _EXT = "py|js|ts|jsx|tsx|svelte|json|yaml|yml|toml|md|css|scss|less|html|htm|sh|bash|rs|go|java|c|cpp|h|cs|rb|php|sql|xml|txt|env|lock|cfg|ini|conf|dockerfile";

/** Matches an entire string that is a file path */
const FILE_PATH_FULL_RE = new RegExp(
  `^(?:[\\w.\\-]+[/\\\\])+[\\w.\\-]+\\.(?:${_EXT})(?::\\d+)?$`
);

/** Finds file paths inside plain text (used for post-processing HTML text nodes) */
const FILE_PATH_TEXT_RE = new RegExp(
  `(?<![\\w/\\\\.])((?:[\\w.\\-]+[/\\\\])+[\\w.\\-]+\\.(?:${_EXT})(?::\\d+)?)(?![\\w])`,
  "g"
);

function _fileLink(path) {
  const esc = path.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  return `<button class="md-file-link" data-path="${path}" title="Open ${path}" onclick="window.__gsOpenFile&&window.__gsOpenFile('${esc}')">${path}</button>`;
}

/** Walk HTML text nodes (not tag contents) and linkify bare file paths */
function _linkifyFilePaths(html) {
  return html.replace(/(<[^>]+>)|([^<]+)/g, (match, tag, text) => {
    if (tag) return tag;
    return text.replace(FILE_PATH_TEXT_RE, (_, path) => _fileLink(path));
  });
}

// ── Custom renderer ───────────────────────────────────────────────────────────

const renderer = new Renderer();

/** Syntax-highlighted fenced code blocks */
renderer.code = ({ text, lang }) => {
  const language = lang && hljs.getLanguage(lang) ? lang : "plaintext";
  const highlighted = hljs.highlight(text, { language }).value;
  const label = lang || "code";
  return `<div class="md-code-block">
  <div class="md-code-header">
    <span class="md-code-lang">${label}</span>
    <button class="md-copy-btn" onclick="navigator.clipboard.writeText(this.closest('.md-code-block').querySelector('code').innerText)">Copy</button>
  </div>
  <pre><code class="hljs language-${language}">${highlighted}</code></pre>
</div>`;
};

/** Inline code — file paths become clickable, everything else stays as code */
renderer.codespan = ({ text }) => {
  if (FILE_PATH_FULL_RE.test(text)) return _fileLink(text);
  return `<code class="md-inline-code">${text}</code>`;
};

/** Links — open in default browser, not in-app */
renderer.link = ({ href, title, text }) =>
  `<a href="${href}" title="${title || ""}" target="_blank" rel="noopener noreferrer">${text}</a>`;

/** Blockquotes */
renderer.blockquote = ({ text }) =>
  `<blockquote class="md-blockquote">${text}</blockquote>`;

// ── Configure marked ──────────────────────────────────────────────────────────

marked.use({
  renderer,
  breaks: true,   // single newline → <br>
  gfm: true,      // GitHub Flavored Markdown (tables, strikethrough, etc.)
});

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Converts a markdown string to sanitised HTML.
 * Only call this for trusted LLM output — not for raw user input.
 *
 * @param {string} text
 * @returns {string} HTML string
 */
export function renderMarkdown(text) {
  if (!text) return "";
  const html = marked.parse(String(text));
  return _linkifyFilePaths(html);
}
