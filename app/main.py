"""
FastAPI application for CV formatting.
Upload a PDF/DOCX -> GPT-4o extraction -> Oxydata-formatted DOCX download.
"""

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import StreamingResponse

from app.text_extractor import extract_text
from app.cv_parser import parse_cv
from app.docx_generator import generate_docx
from app import airtable_client

load_dotenv()

app = FastAPI(title="Oxydata CV Formatter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "cv_formatter_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
DOWNLOAD_FILENAME_MAP: dict[str, str] = {}

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _public_base_url(request: Request) -> str:
    """Public origin for attachment URLs. Empty APP_URL in .env must fall back to the request host."""
    explicit = (os.environ.get("APP_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    return str(request.base_url).rstrip("/")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.post("/api/convert")
async def convert_cv(file: UploadFile = File(...)):
    _validate_upload(file)

    # Save uploaded file
    file_id = uuid.uuid4().hex
    ext = Path(file.filename).suffix.lower()
    upload_path = UPLOAD_DIR / f"{file_id}{ext}"

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds 20 MB limit.")
    upload_path.write_bytes(content)

    try:
        try:
            raw_text = extract_text(str(upload_path))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not raw_text.strip():
            raise HTTPException(422, "Could not extract any text from the uploaded file.")

        cv_data = parse_cv(raw_text)

        filename = _build_output_filename(cv_data.name)
        output_path = UPLOAD_DIR / f"{file_id}_formatted.docx"
        generate_docx(cv_data, str(output_path))

        return FileResponse(
            path=str(output_path),
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    finally:
        upload_path.unlink(missing_ok=True)


@app.post("/api/convert-stream")
async def convert_cv_stream(file: UploadFile = File(...)):
    """SSE endpoint that streams progress events, then the download URL."""
    _validate_upload(file)

    file_id = uuid.uuid4().hex
    ext = Path(file.filename).suffix.lower()
    upload_path = UPLOAD_DIR / f"{file_id}{ext}"

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds 20 MB limit.")
    upload_path.write_bytes(content)

    async def event_generator():
        try:
            # Step 1: Extract text
            yield _sse("progress", {"step": 1, "message": "Extracting text from your CV..."})
            await asyncio.sleep(0.1)

            try:
                raw_text = extract_text(str(upload_path))
            except ValueError as exc:
                yield _sse("error", {"message": str(exc)})
                return
            if not raw_text.strip():
                yield _sse("error", {"message": "Could not extract any text from the uploaded file."})
                return

            # Step 2: AI analysis
            yield _sse("progress", {"step": 2, "message": "Analyzing CV content with AI..."})
            await asyncio.sleep(0.1)

            cv_data = await asyncio.to_thread(parse_cv, raw_text)

            # Step 3: Generate document
            yield _sse("progress", {"step": 3, "message": "Generating formatted document..."})
            await asyncio.sleep(0.1)

            filename = _build_output_filename(cv_data.name)
            output_path = UPLOAD_DIR / f"{file_id}_formatted.docx"
            await asyncio.to_thread(generate_docx, cv_data, str(output_path))
            DOWNLOAD_FILENAME_MAP[file_id] = filename

            # Step 4: Done
            yield _sse("done", {
                "step": 4,
                "message": "Done! Your formatted CV is ready.",
                "download_url": f"/api/download/{file_id}",
                "filename": filename,
            })

        except Exception as e:
            yield _sse("error", {"message": str(e)})
        finally:
            upload_path.unlink(missing_ok=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/download/{file_id}")
async def download_file(file_id: str):
    output_path = UPLOAD_DIR / f"{file_id}_formatted.docx"
    if not output_path.exists():
        raise HTTPException(404, "File not found or already downloaded.")

    filename = DOWNLOAD_FILENAME_MAP.pop(file_id, "FirstName_LastName_OxyCVFormat.docx")
    return FileResponse(
        path=str(output_path),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/airtable/process", response_class=HTMLResponse)
async def airtable_process_page():
    page = STATIC_DIR / "airtable_process.html"
    return HTMLResponse(content=page.read_text(encoding="utf-8"))


@app.get("/airtable/process-stream")
async def airtable_process_stream(
    request: Request,
    record_id: str = Query(..., description="Airtable record ID"),
):
    """SSE endpoint: fetch CV from Airtable, process it, upload result back."""

    async def event_generator():
        upload_path = None
        try:
            # Step 1: Fetch record from Airtable
            yield _sse("progress", {"step": 1, "message": "Fetching CV from Airtable..."})
            await asyncio.sleep(0.1)

            try:
                record = await asyncio.to_thread(airtable_client.fetch_record, record_id)
            except Exception as exc:
                yield _sse("error", {"message": f"Failed to fetch Airtable record: {exc}"})
                return

            cv_url = record["cv_url"]
            cv_filename = record["cv_filename"]
            job_name = record["job_name"]

            file_id = uuid.uuid4().hex
            ext = Path(cv_filename).suffix.lower() or ".pdf"
            upload_path = UPLOAD_DIR / f"{file_id}{ext}"

            try:
                await asyncio.to_thread(airtable_client.download_attachment, cv_url, str(upload_path))
            except Exception as exc:
                yield _sse("error", {"message": f"Failed to download CV file: {exc}"})
                return

            # Step 2: Extract text
            yield _sse("progress", {"step": 2, "message": "Extracting text from CV..."})
            await asyncio.sleep(0.1)

            try:
                raw_text = extract_text(str(upload_path))
            except ValueError as exc:
                yield _sse("error", {"message": str(exc)})
                return
            if not raw_text.strip():
                yield _sse("error", {"message": "Could not extract any text from the CV file."})
                return

            # Step 3: AI analysis
            yield _sse("progress", {"step": 3, "message": "Analyzing CV content with AI..."})
            await asyncio.sleep(0.1)

            cv_data = await asyncio.to_thread(parse_cv, raw_text)
            cv_data.position_applied = job_name

            # Step 4: Generate document
            yield _sse("progress", {"step": 4, "message": "Generating formatted document..."})
            await asyncio.sleep(0.1)

            filename = _build_output_filename(cv_data.name)
            output_path = UPLOAD_DIR / f"{file_id}_formatted.docx"
            await asyncio.to_thread(generate_docx, cv_data, str(output_path))
            DOWNLOAD_FILENAME_MAP[file_id] = filename

            # Step 5: Upload to Airtable
            yield _sse("progress", {"step": 5, "message": "Uploading result to Airtable..."})
            await asyncio.sleep(0.1)

            app_url = _public_base_url(request)
            download_url = f"{app_url}/api/download/{file_id}"
            if not download_url.lower().startswith(("http://", "https://")):
                yield _sse(
                    "error",
                    {
                        "message": "Invalid public URL for the formatted file. Set APP_URL in .env to your HTTPS app URL (e.g. Render or ngrok) so Airtable can download it.",
                    },
                )
                return
            host = (urlparse(download_url).hostname or "").lower()
            if host in ("127.0.0.1", "localhost", "::1") or host.endswith(".local"):
                yield _sse(
                    "error",
                    {
                        "message": "Airtable cannot fetch files from your computer. Set APP_URL in .env to a public HTTPS URL (e.g. your Render service, or ngrok/Cloudflare Tunnel pointing at this port), then retry.",
                    },
                )
                return

            try:
                await asyncio.to_thread(
                    airtable_client.upload_result, record_id, download_url, filename
                )
            except Exception as exc:
                yield _sse("error", {"message": f"Failed to upload to Airtable: {exc}"})
                return

            yield _sse("done", {
                "step": 5,
                "message": "Done! The formatted CV has been uploaded to Airtable.",
            })

        except Exception as e:
            yield _sse("error", {"message": str(e)})
        finally:
            if upload_path:
                upload_path.unlink(missing_ok=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _validate_upload(file: UploadFile):
    if not file.filename:
        raise HTTPException(400, "No file provided.")
    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".docx"):
        raise HTTPException(
            400,
            f"Unsupported file type '{ext}'. Please upload a PDF or DOCX file.",
        )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _build_output_filename(full_name: str) -> str:
    cleaned_parts = [part for part in full_name.strip().split() if part]
    if not cleaned_parts:
        return "FirstName_LastName_OxyCVFormat.docx"

    first_name = cleaned_parts[0]
    if len(cleaned_parts) == 1:
        return f"{first_name}_OxyCVFormat.docx"
    last_name = cleaned_parts[-1]
    return f"{first_name}_{last_name}_OxyCVFormat.docx"
