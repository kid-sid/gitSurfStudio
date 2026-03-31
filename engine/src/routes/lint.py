"""Lint routes: real-time code linting and AI lint fix."""

import os

from fastapi import APIRouter, HTTPException, Request

from src.engine_state import state
from src.models import FixLintRequest, LintRequest
from src.routes import limiter, logger

router = APIRouter()


@router.post("/lint")
@limiter.limit("120/minute")
async def lint_code(request: Request, req: LintRequest):
    """Real-time lint — pipes editor content to ruff (Python) or eslint (JS/TS)."""
    from src.tools.lint_tool import LintTool
    tool = LintTool()
    try:
        diagnostics = tool.lint_content(req.content, req.file_path, req.workspace)
        return {"diagnostics": diagnostics}
    except Exception as e:
        logger.warning("Lint endpoint error: %s", e)
        return {"diagnostics": []}


@router.post("/fix-lint")
@limiter.limit("30/minute")
async def fix_lint(request: Request, req: FixLintRequest):
    """Use the LLM to fix lint diagnostics in the given code."""
    if not state.llm:
        raise HTTPException(status_code=503, detail="LLM not initialized")

    ext = os.path.splitext(req.file_path)[1].lower()
    lang_map = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".jsx": "JavaScript (JSX)", ".tsx": "TypeScript (TSX)",
    }
    lang = lang_map.get(ext, "code")

    diag_text = "\n".join(
        f"  Line {d.get('line', '?')}: [{d.get('source', '')}] "
        f"{d.get('message', '')} ({d.get('code', '')})"
        for d in req.diagnostics
    )

    prompt = (
        f"Fix the following lint errors in this {lang} file.\n"
        f"Return ONLY the corrected code, no explanations, "
        f"no markdown fences.\n\n"
        f"Lint errors:\n{diag_text}\n\n"
        f"Code:\n{req.content}"
    )

    try:
        fixed = state.llm._call(
            messages=[{"role": "user", "content": prompt}],
            model=state.llm.fast_model,
            temperature=0.0,
            max_tokens=4096,
        )
        # Strip any accidental markdown fences
        fixed = fixed.strip()
        if fixed.startswith("```"):
            lines = fixed.split("\n")
            # Remove first and last fence lines
            lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            fixed = "\n".join(lines)
        return {"fixed_content": fixed}
    except Exception as e:
        logger.error("fix-lint error: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
