"""Symbol routes: symbol extraction and peek definition."""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException

from src.engine_state import _safe_path, state
from src.models import SymbolResponse

router = APIRouter()


@router.get("/symbols", response_model=SymbolResponse)
async def get_symbols(path: str, workspace: Optional[str] = None):
    """
    Extracts symbols (classes, functions) from a file or directory.
    If 'workspace' is provided, 'path' is treated as relative to it.
    """
    effective_workspace = workspace or state.workspace_path
    if effective_workspace:
        full_path = os.path.join(effective_workspace, path) if workspace else path
        target_path = _safe_path(effective_workspace, full_path)
    else:
        target_path = os.path.realpath(path)

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if os.path.isfile(target_path):
            ext = os.path.splitext(target_path)[1].lower()
            if ext in state.symbol_extractor.PYTHON_EXTENSIONS:
                symbols = state.symbol_extractor._extract_python(target_path)
            elif ext in state.symbol_extractor.JS_EXTENSIONS:
                symbols = state.symbol_extractor._extract_js(target_path)
            elif ext in state.symbol_extractor.C_FAMILY_EXTENSIONS:
                symbols = state.symbol_extractor._extract_c_family(target_path)
            else:
                symbols = []
            return {"path": path, "symbols": symbols}
        else:
            # Use mtime-keyed cache to avoid re-running on every request
            try:
                mtime = os.path.getmtime(target_path)
            except OSError:
                mtime = None

            cached = state._symbols_cache.get(target_path)
            if cached is not None and mtime is not None and cached[1] == mtime:
                raw_symbols = cached[0]
            else:
                raw_symbols = state.symbol_extractor.extract_from_directory(target_path)
                if mtime is not None:
                    state._symbols_cache[target_path] = (raw_symbols, mtime)
            # Flatten dict into a list
            symbols = []
            for file_path, file_symbols in raw_symbols.items():
                for sym in file_symbols:
                    sym_with_file = dict(sym)
                    sym_with_file["file"] = file_path
                    symbols.append(sym_with_file)
            return {"path": path, "symbols": symbols}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/peek-symbol")
async def peek_symbol_endpoint(name: str):
    """
    Returns the source block(s) for a symbol name — the F12 / Peek Definition backend.
    """
    if not state.workspace_path:
        raise HTTPException(status_code=400, detail="No workspace initialized")
    tool = state.agent_tools.get("SymbolPeekerTool")
    if not tool:
        raise HTTPException(status_code=503, detail="SymbolPeekerTool not available")
    try:
        results = tool.peek_symbol(name)
        return {"symbol": name, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
