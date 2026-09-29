"""Idempotent shared-drive evidence folders."""
import asyncio
import json
import logging

import httpx
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models import Portfolio, Proposal
from api.portfolio import committee_portfolio

logger = logging.getLogger(__name__)
BASE = "https://www.googleapis.com/drive/v3"


def folder_url(proposal):
    if not proposal.drive_ready or not proposal.drive_folder_id:
        return None
    if proposal.drive_folder_id.startswith("preview-"):
        return None
    return f"https://drive.google.com/drive/folders/{proposal.drive_folder_id}"


def _access_token():
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    settings = get_settings()
    options = {"scopes": ["https://www.googleapis.com/auth/drive.file"]}
    if settings.google_service_account_json:
        credentials = service_account.Credentials.from_service_account_info(json.loads(settings.google_service_account_json), **options)
    else:
        credentials = service_account.Credentials.from_service_account_file(settings.google_service_account_file, **options)
    credentials.refresh(Request())
    return credentials.token


async def provision_evidence(db: Session, proposal_id: int):
    settings = get_settings()
    if not settings.grading_enabled or settings.google_drive_mode == "disabled":
        return
    proposal = db.query(Proposal).filter_by(id=proposal_id).with_for_update().one()
    if settings.google_drive_mode != "live":
        return
    try:
        parent = (settings.google_drive_welfare_parent_folder_id if committee_portfolio(proposal.committee) == Portfolio.welfare else settings.google_drive_social_parent_folder_id) or settings.google_drive_parent_folder_id
        if not parent:
            raise ValueError("Drive parent folder is not configured")
        token = await asyncio.to_thread(_access_token)
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=45) as client:
            if not proposal.drive_folder_id:
                response = await client.get(f"{BASE}/files", params={
                    "q": f"'{parent}' in parents and trashed = false and appProperties has {{ key = 'proposal_id' and value = '{proposal.id}' }}",
                    "spaces": "drive", "includeItemsFromAllDrives": "true", "supportsAllDrives": "true",
                    "fields": "files(id,name,mimeType)",
                })
                response.raise_for_status()
                existing_folder = next((item for item in response.json().get("files", []) if item.get("mimeType") == "application/vnd.google-apps.folder"), None)
                if existing_folder:
                    proposal.drive_folder_id = existing_folder["id"]
                else:
                    response = await client.post(f"{BASE}/files", params={"supportsAllDrives": "true"}, json={
                        "name": f"{proposal.committee.name}-{proposal.title}",
                        "mimeType": "application/vnd.google-apps.folder", "parents": [parent],
                        "appProperties": {"proposal_id": str(proposal.id)},
                    })
                    response.raise_for_status()
                    proposal.drive_folder_id = response.json()["id"]
                db.commit()
            if not proposal.drive_ready:
                proposal.drive_ready = True
                db.commit()
        proposal.drive_error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        # Don't expose credentials/provider responses to users. Folder setup is
        # retried by the scheduler when the provider is temporarily unavailable.
        proposal = db.get(Proposal, proposal_id)
        proposal.drive_error = "Evidence folder setup is incomplete and will be retried."
        db.commit()
        detail = str(exc).replace("\n", " ")
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f"Google Drive returned HTTP {exc.response.status_code}: {exc.response.text}"
        if len(detail) > 500:
            detail = detail[:500] + "…"
        logger.warning("Evidence setup failed for proposal %s: %s", proposal_id, detail)
