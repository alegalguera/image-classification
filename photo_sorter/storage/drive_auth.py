"""Google OAuth for the Drive backend.

Local CLI: installed-app flow (browser opens once), token saved under ~/.config/photo-sorter/.
Web app:   authorization-code flow with a redirect URI; the token travels in the user's session.
Needs an OAuth client from the Google Cloud console (APIs & Services > Credentials):
  * "Desktop app" client -> download JSON -> client_secret.json (CLI)
  * "Web application" client with redirect URIs http://localhost:8080/auth/callback and the Cloud Run URL
The `drive` scope is required: `drive.file` cannot move files the app did not create.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import APP_DIR as CONFIG_DIR

SCOPES = ["https://www.googleapis.com/auth/drive", "openid", "https://www.googleapis.com/auth/userinfo.email"]
TOKEN_PATH = CONFIG_DIR / "token.json"


def _imports():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow, InstalledAppFlow
    except ImportError as e:
        raise ImportError("Google Drive needs the drive extra: run `uv sync --extra drive`") from e
    return Request, Credentials, Flow, InstalledAppFlow


def client_config() -> dict:
    """OAuth client JSON from $GOOGLE_OAUTH_CLIENT_JSON (contents or path) or ~/.config/photo-sorter/client_secret.json."""
    raw = os.environ.get("GOOGLE_OAUTH_CLIENT_JSON")
    if raw:
        return json.loads(raw) if raw.lstrip().startswith("{") else json.loads(Path(raw).read_text(encoding="utf-8"))
    p = CONFIG_DIR / "client_secret.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"No OAuth client found. Create one in the Google Cloud console and save it as {p} "
        "(or set GOOGLE_OAUTH_CLIENT_JSON). See README > Google Cloud setup."
    )


def has_client() -> bool:
    """Is an OAuth client configured (needed for any Google sign-in)?"""
    try:
        client_config()
        return True
    except (FileNotFoundError, ValueError):
        return False


def load_token(path: Path = TOKEN_PATH):
    """Saved CLI credentials, refreshed if needed; None when not logged in."""
    Request, Credentials, _, _ = _imports()
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    return creds if creds.valid else None


def login_installed(path: Path = TOKEN_PATH):
    """Open the browser once and store the token for later CLI runs."""
    _, _, _, InstalledAppFlow = _imports()
    flow = InstalledAppFlow.from_client_config(client_config(), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return creds


def credentials_from_json(data: str):
    """Rebuild credentials from a token JSON string (web sessions, Secret Manager)."""
    Request, Credentials, _, _ = _imports()
    creds = Credentials.from_authorized_user_info(json.loads(data), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def web_flow(redirect_uri: str, state: str | None = None, code_verifier: str | None = None):
    """Authorization-code flow for the web app. Returns the Flow; call .authorization_url() then .fetch_token().
    The flow uses PKCE: keep flow.code_verifier from the first step and pass it back in for the callback."""
    _, _, Flow, _ = _imports()
    extra = {"code_verifier": code_verifier} if code_verifier else {}
    return Flow.from_client_config(client_config(), SCOPES, redirect_uri=redirect_uri, state=state, **extra)


def logout(path: Path = TOKEN_PATH) -> bool:
    if path.exists():
        path.unlink()
        return True
    return False
