"""Auth routes: GitHub OAuth login flow."""

import os

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from src.engine_state import state

router = APIRouter(prefix="/auth")


@router.get("/status")
async def auth_status():
    return {"authenticated": state.github_token is not None}


@router.get("/login")
async def auth_login():
    client_id = os.getenv("GITHUB_CLIENT_ID")
    if not client_id:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID not set")

    scope = "repo,user"
    github_url = f"https://github.com/login/oauth/authorize?client_id={client_id}&scope={scope}"
    return RedirectResponse(github_url)


@router.get("/callback")
async def auth_callback(code: str):
    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="OAuth credentials not set")

    # Exchange code for token
    token_url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }

    response = requests.post(token_url, json=params, headers=headers)
    token_data = response.json()

    if "access_token" in token_data:
        state.github_token = token_data["access_token"]
        _body_style = (
            "font-family:sans-serif;display:flex;"
            "align-items:center;justify-content:center;"
            "height:100vh;background:#0d1117;color:white"
        )
        return HTMLResponse(
            "<html>"
            f'<body style="{_body_style}">'
            '<div style="text-align:center;">'
            "<h1>GitSurf Studio</h1>"
            '<p style="color:#63ff63;">Authenticated successfully!</p>'
            "<p>You can close this tab and return to the IDE.</p>"
            "<script>setTimeout(()=>window.close(),3000);</script>"
            "</div></body></html>"
        )
    else:
        error_msg = token_data.get("error_description", "Failed to exchange token")
        raise HTTPException(status_code=400, detail=error_msg)
