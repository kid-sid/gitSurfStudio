import os
import requests
from bs4 import BeautifulSoup
from typing import Optional

# Official documentation URLs for known libraries.
# Tuple: (base_docs_url, site_domain_for_search_scoping)
_DOCS_URL_MAP: dict[str, tuple[str, str]] = {
    # Frontend frameworks
    "next.js":          ("https://nextjs.org/docs", "nextjs.org"),
    "nextjs":           ("https://nextjs.org/docs", "nextjs.org"),
    "react":            ("https://react.dev/reference/react", "react.dev"),
    "vue.js":           ("https://vuejs.org/guide", "vuejs.org"),
    "vue":              ("https://vuejs.org/guide", "vuejs.org"),
    "nuxt":             ("https://nuxt.com/docs", "nuxt.com"),
    "svelte":           ("https://svelte.dev/docs/svelte", "svelte.dev"),
    "sveltekit":        ("https://svelte.dev/docs/kit", "svelte.dev"),
    "remix":            ("https://remix.run/docs/en/main", "remix.run"),
    "astro":            ("https://docs.astro.build/en/getting-started", "docs.astro.build"),
    # Node servers
    "express":          ("https://expressjs.com/en/5x/api.html", "expressjs.com"),
    "fastify":          ("https://fastify.dev/docs/latest", "fastify.dev"),
    "hono":             ("https://hono.dev/docs", "hono.dev"),
    # CSS / UI
    "tailwind css":     ("https://tailwindcss.com/docs", "tailwindcss.com"),
    "tailwind":         ("https://tailwindcss.com/docs", "tailwindcss.com"),
    "shadcn/ui":        ("https://ui.shadcn.com/docs", "ui.shadcn.com"),
    "shadcn":           ("https://ui.shadcn.com/docs", "ui.shadcn.com"),
    "radix ui":         ("https://www.radix-ui.com/primitives/docs", "radix-ui.com"),
    "framer motion":    ("https://www.framer.com/motion/introduction", "framer.com"),
    # State / data fetching
    "react query":      ("https://tanstack.com/query/latest/docs/framework/react/overview", "tanstack.com"),
    "tanstack query":   ("https://tanstack.com/query/latest/docs/framework/react/overview", "tanstack.com"),
    "zustand":          ("https://docs.pmnd.rs/zustand/getting-started/introduction", "docs.pmnd.rs"),
    "jotai":            ("https://jotai.org/docs/introduction", "jotai.org"),
    "zod":              ("https://zod.dev", "zod.dev"),
    # Build tools
    "vite":             ("https://vite.dev/guide", "vite.dev"),
    "vitest":           ("https://vitest.dev/guide", "vitest.dev"),
    "bun":              ("https://bun.sh/docs", "bun.sh"),
    "deno":             ("https://docs.deno.com/runtime", "docs.deno.com"),
    "turborepo":        ("https://turbo.build/repo/docs", "turbo.build"),
    # Python backends
    "fastapi":          ("https://fastapi.tiangolo.com", "fastapi.tiangolo.com"),
    "django":           ("https://docs.djangoproject.com/en/stable", "docs.djangoproject.com"),
    "flask":            ("https://flask.palletsprojects.com/en/stable", "flask.palletsprojects.com"),
    "pydantic":         ("https://docs.pydantic.dev/latest", "docs.pydantic.dev"),
    "pydantic v2":      ("https://docs.pydantic.dev/latest", "docs.pydantic.dev"),
    "sqlalchemy":       ("https://docs.sqlalchemy.org/en/20", "docs.sqlalchemy.org"),
    "alembic":          ("https://alembic.sqlalchemy.org/en/latest", "alembic.sqlalchemy.org"),
    "celery":           ("https://docs.celeryq.dev/en/stable", "docs.celeryq.dev"),
    # AI / LLM
    "langchain":        ("https://python.langchain.com/docs/introduction", "python.langchain.com"),
    "langgraph":        ("https://langchain-ai.github.io/langgraph/tutorials/introduction", "langchain-ai.github.io"),
    "llamaindex":       ("https://docs.llamaindex.ai/en/stable", "docs.llamaindex.ai"),
    "openai":           ("https://platform.openai.com/docs/overview", "platform.openai.com"),
    "anthropic":        ("https://docs.anthropic.com/en/docs/overview", "docs.anthropic.com"),
    "hugging face":     ("https://huggingface.co/docs/transformers/index", "huggingface.co"),
    "transformers":     ("https://huggingface.co/docs/transformers/index", "huggingface.co"),
    # ORMs / databases
    "prisma":           ("https://www.prisma.io/docs/getting-started", "prisma.io"),
    "drizzle":          ("https://orm.drizzle.team/docs/overview", "orm.drizzle.team"),
    "drizzle orm":      ("https://orm.drizzle.team/docs/overview", "orm.drizzle.team"),
    "mongoose":         ("https://mongoosejs.com/docs/guide.html", "mongoosejs.com"),
    "supabase":         ("https://supabase.com/docs", "supabase.com"),
    "firebase":         ("https://firebase.google.com/docs", "firebase.google.com"),
    # Desktop / infra
    "tauri":            ("https://v2.tauri.app/reference", "v2.tauri.app"),
    "terraform":        ("https://developer.hashicorp.com/terraform/docs", "developer.hashicorp.com"),
    "pulumi":           ("https://www.pulumi.com/docs", "pulumi.com"),
    "docker":           ("https://docs.docker.com", "docs.docker.com"),
    # Rust
    "axum":             ("https://docs.rs/axum/latest/axum", "docs.rs"),
    "tokio":            ("https://tokio.rs/tokio/tutorial", "tokio.rs"),
    "serde":            ("https://serde.rs", "serde.rs"),
    "sqlx":             ("https://docs.rs/sqlx/latest/sqlx", "docs.rs"),
    "actix web":        ("https://actix.rs/docs", "actix.rs"),
    "actix":            ("https://actix.rs/docs", "actix.rs"),
    # Misc
    "vercel ai sdk":    ("https://sdk.vercel.ai/docs/introduction", "sdk.vercel.ai"),
}


class WebSearchTool:
    """
    Allows the agent to search the web and fetch content from URLs.
    Useful for looking up documentation or solving version-specific errors.
    """

    def __init__(self, tavily_api_key: Optional[str] = None):
        self.tavily_api_key = tavily_api_key or os.getenv("TAVILY_API_KEY")

    def search(self, query: str, num_results: int = 5) -> str:
        """
        Performs a web search using Tavily API if available.
        """
        if not self.tavily_api_key:
            return "[Error] TAVILY_API_KEY not found. Web search is disabled."

        try:
            url = "https://api.tavily.com/search"
            payload = {
                "api_key": self.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": num_results
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for res in data.get("results", []):
                results.append(f"Title: {res['title']}\nURL: {res['url']}\nSnippet: {res['content']}\n")
            
            return "\n---\n".join(results)
        except Exception as e:
            return f"[Error] Web search failed: {e}"

    def fetch_docs(self, library: str, topic: str) -> str:
        """
        Fetch official documentation for a library on a specific topic.

        Strategy (in order):
        1. Tavily site-scoped search  → targeted snippets + full page of top result
        2. Topic-specific URL         → derive a direct page URL from the topic slug
        3. Base docs URL              → last resort; rejected if topic keywords absent
        If all three fail, returns a clear [Error] so the agent does not retry blindly.
        """
        lib_key = library.lower().strip()
        entry = _DOCS_URL_MAP.get(lib_key)
        site_domain = entry[1] if entry else None
        base_url = entry[0] if entry else None

        # Helper: check whether fetched content is actually relevant to the topic
        def _is_relevant(text: str) -> bool:
            keywords = [w.lower().strip("$()[]") for w in topic.split() if len(w) > 2]
            return any(kw in text.lower() for kw in keywords)

        # ── 1. Tavily site-scoped search ────────────────────────────────────
        if self.tavily_api_key:
            query = f"site:{site_domain} {topic}" if site_domain else f"{library} {topic} documentation"
            try:
                payload = {
                    "api_key": self.tavily_api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "include_answer": True,
                    "max_results": 3,
                }
                resp = requests.post("https://api.tavily.com/search", json=payload, timeout=12)
                resp.raise_for_status()
                data = resp.json()

                parts = []
                if data.get("answer"):
                    parts.append(f"### Summary\n{data['answer']}\n")

                top_url = None
                for res in data.get("results", []):
                    parts.append(f"**{res['title']}**\nSource: {res['url']}\n{res['content']}\n")
                    if top_url is None:
                        top_url = res["url"]

                if top_url:
                    full = self.fetch_url(top_url)
                    if not full.startswith("[Error]") and len(full) > 300:
                        parts.append(f"### Full page: {top_url}\n{full[:6000]}")

                if parts:
                    return "\n---\n".join(parts)
            except Exception as e:
                pass  # log silently and fall through — Tavily error reported below

        tavily_status = "" if self.tavily_api_key else " (TAVILY_API_KEY not set)"

        # ── 2. Topic-specific URL ────────────────────────────────────────────
        # Derive a candidate page URL from the first meaningful topic token
        # e.g. topic="$state runes" → slug="$state" → base_url + "/$state"
        if base_url:
            topic_slug = topic.strip().split()[0] if topic.strip() else ""
            if topic_slug:
                specific_url = base_url.rstrip("/") + "/" + topic_slug
                content = self.fetch_url(specific_url)
                if not content.startswith("[Error]") and _is_relevant(content):
                    return f"Documentation from {specific_url}:\n{content[:8000]}"

        # ── 3. Base docs URL (last resort) ──────────────────────────────────
        if base_url:
            content = self.fetch_url(base_url)
            if not content.startswith("[Error]"):
                if _is_relevant(content):
                    return f"Documentation from {base_url}:\n{content[:8000]}"
                # Page loaded but it's just a generic overview — not useful
                return (
                    f"[Error] fetch_docs({library!r}, {topic!r}) fetched {base_url} "
                    f"but the page does not contain content about '{topic}'{tavily_status}. "
                    f"Use WebSearchTool.search(query='{library} {topic} documentation') instead."
                )

        return (
            f"[Error] fetch_docs({library!r}, {topic!r}): no docs URL known for '{library}'{tavily_status}. "
            f"Use WebSearchTool.search(query='{library} {topic} documentation') instead."
        )

    def fetch_url(self, url: str) -> str:
        """
        Fetches the content of a URL and converts it to basic text/markdown.
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            for element in soup(['script', 'style', 'nav', 'header', 'footer']):
                element.decompose()
            
            text = soup.get_text(separator='\n')
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            clean_text = "\n".join(lines)
            
            return clean_text[:10000] 
        except Exception as e:
            return f"[Error] Failed to fetch URL {url}: {e}"

if __name__ == "__main__":
    # Simple Test
    tool = WebSearchTool()
    print("Testing WebSearchTool Fetch...")
    # Fetching a simple page
    content = tool.fetch_url("https://example.com")
    print(f"Content length: {len(content)}")
    print(f"Preview: {content[:100]}")
