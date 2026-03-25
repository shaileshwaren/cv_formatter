"""
Airtable REST API client for fetching CV records and uploading formatted results.
"""

import os
from pathlib import Path

import requests


def _api_root() -> str:
    base_id = os.environ.get("AIRTABLE_BASE_ID", "").strip()
    table_id = os.environ.get("AIRTABLE_TABLE_ID", "").strip()
    if not base_id or not table_id:
        raise RuntimeError(
            "AIRTABLE_BASE_ID and AIRTABLE_TABLE_ID environment variables must be set."
        )
    return f"https://api.airtable.com/v0/{base_id}/{table_id}"


def _headers() -> dict[str, str]:
    pat = os.environ.get("AIRTABLE_PAT", "") or os.environ.get("AIRTABLE_TOKEN", "")
    if not pat:
        raise RuntimeError("AIRTABLE_PAT (or AIRTABLE_TOKEN) environment variable is not set.")
    return {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
    }


def fetch_record(record_id: str) -> dict:
    """Fetch a single record and return the CV attachment URL, filename, and job name."""
    resp = requests.get(f"{_api_root()}/{record_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    fields = resp.json().get("fields", {})

    attachments = fields.get("CV", [])
    if not attachments:
        raise ValueError("No CV attachment found in the 'CV' field for this record.")

    attachment = attachments[0]
    cv_url = attachment.get("url", "")
    cv_filename = attachment.get("filename", "cv.pdf")
    if not cv_url:
        raise ValueError("CV attachment has no download URL.")

    job_name = fields.get("job_name", "").strip() or "<To be filled>"

    return {
        "cv_url": cv_url,
        "cv_filename": cv_filename,
        "job_name": job_name,
    }


MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # match main.py upload limit


def download_attachment(url: str, dest_path: str) -> None:
    """Download a file from a URL to a local path."""
    headers = {"User-Agent": "OxydataCVFormatter/1.0"}
    resp = requests.get(url, timeout=120, headers=headers)
    resp.raise_for_status()
    data = resp.content
    if not data:
        raise ValueError(
            "Downloaded CV file is empty. Try re-uploading the CV in Airtable, or the attachment link may have expired."
        )
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"CV file exceeds {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit.")
    Path(dest_path).write_bytes(data)


def upload_result(record_id: str, file_url: str, filename: str) -> None:
    """Update the record's 'cv_oxy' attachment field with the formatted DOCX URL."""
    payload = {
        "fields": {
            "cv_oxy": [{"url": file_url, "filename": filename}],
        }
    }
    resp = requests.patch(
        f"{_api_root()}/{record_id}",
        headers=_headers(),
        json=payload,
        timeout=60,
    )
    if not resp.ok:
        detail = resp.text[:500] if resp.text else resp.reason
        raise RuntimeError(f"Airtable API {resp.status_code}: {detail}")
