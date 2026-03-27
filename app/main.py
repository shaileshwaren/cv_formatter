"""
FastAPI application for CV formatting.
Upload a PDF/DOCX -> GPT-4o extraction -> Oxydata-formatted DOCX download.
"""

import asyncio
import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime
from time import perf_counter
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask
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
BATCH_ZIP_FILENAME_MAP: dict[str, str] = {}

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


@app.post("/api/convert-batch-stream")
async def convert_cv_batch_stream(files: list[UploadFile] = File(...)):
    """Render-only SSE endpoint for sequential multi-CV processing."""
    if not _is_render_environment():
        raise HTTPException(403, "Batch processing is only enabled on Render.")
    if not files:
        raise HTTPException(400, "No files provided.")

    uploads: list[dict[str, str]] = []
    try:
        for file in files:
            _validate_upload(file)
            file_id = uuid.uuid4().hex
            ext = Path(file.filename).suffix.lower()
            upload_path = UPLOAD_DIR / f"{file_id}{ext}"

            content = await file.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(413, f"File '{file.filename}' exceeds 20 MB limit.")
            upload_path.write_bytes(content)
            uploads.append({
                "filename": file.filename,
                "file_id": file_id,
                "upload_path": str(upload_path),
            })
    except Exception:
        for item in uploads:
            Path(item["upload_path"]).unlink(missing_ok=True)
        raise

    async def event_generator():
        generated_outputs: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        upload_paths = [Path(item["upload_path"]) for item in uploads]
        completed_durations: list[float] = []
        total = len(uploads)
        processed = 0

        try:
            yield _sse("batch_start", {"total": total})

            for index, item in enumerate(uploads, start=1):
                filename = item["filename"]
                file_id = item["file_id"]
                upload_path = Path(item["upload_path"])
                started_at = perf_counter()

                yield _sse(
                    "file_start",
                    {
                        "index": index,
                        "total": total,
                        "filename": filename,
                        "processed": processed,
                        "eta_seconds": _estimate_eta_seconds(completed_durations, total, processed),
                    },
                )

                # Step 1
                yield _sse("file_progress", {"step": 1, "message": "Extracting text from your CV..."})
                await asyncio.sleep(0.1)
                try:
                    raw_text = extract_text(str(upload_path))
                except ValueError as exc:
                    processed += 1
                    completed_durations.append(perf_counter() - started_at)
                    failures.append({"filename": filename, "message": str(exc)})
                    yield _sse(
                        "file_error",
                        {
                            "filename": filename,
                            "message": str(exc),
                            "processed": processed,
                            "total": total,
                            "eta_seconds": _estimate_eta_seconds(completed_durations, total, processed),
                        },
                    )
                    continue
                if not raw_text.strip():
                    processed += 1
                    completed_durations.append(perf_counter() - started_at)
                    failures.append(
                        {
                            "filename": filename,
                            "message": "Could not extract any text from the uploaded file.",
                        }
                    )
                    yield _sse(
                        "file_error",
                        {
                            "filename": filename,
                            "message": "Could not extract any text from the uploaded file.",
                            "processed": processed,
                            "total": total,
                            "eta_seconds": _estimate_eta_seconds(completed_durations, total, processed),
                        },
                    )
                    continue

                # Step 2
                yield _sse("file_progress", {"step": 2, "message": "Analyzing CV content with AI..."})
                await asyncio.sleep(0.1)
                try:
                    cv_data = await asyncio.to_thread(parse_cv, raw_text)
                except Exception as exc:
                    processed += 1
                    completed_durations.append(perf_counter() - started_at)
                    failures.append({"filename": filename, "message": f"AI analysis failed: {exc}"})
                    yield _sse(
                        "file_error",
                        {
                            "filename": filename,
                            "message": f"AI analysis failed: {exc}",
                            "processed": processed,
                            "total": total,
                            "eta_seconds": _estimate_eta_seconds(completed_durations, total, processed),
                        },
                    )
                    continue

                # Step 3
                yield _sse("file_progress", {"step": 3, "message": "Generating formatted document..."})
                await asyncio.sleep(0.1)
                output_filename = _build_output_filename(cv_data.name)
                output_path = UPLOAD_DIR / f"{file_id}_formatted.docx"
                try:
                    await asyncio.to_thread(generate_docx, cv_data, str(output_path))
                except Exception as exc:
                    processed += 1
                    completed_durations.append(perf_counter() - started_at)
                    failures.append({"filename": filename, "message": f"DOCX generation failed: {exc}"})
                    yield _sse(
                        "file_error",
                        {
                            "filename": filename,
                            "message": f"DOCX generation failed: {exc}",
                            "processed": processed,
                            "total": total,
                            "eta_seconds": _estimate_eta_seconds(completed_durations, total, processed),
                        },
                    )
                    continue

                processed += 1
                completed_durations.append(perf_counter() - started_at)
                generated_outputs.append(
                    {
                        "source_filename": filename,
                        "output_filename": output_filename,
                        "output_path": str(output_path),
                    }
                )

                yield _sse(
                    "file_done",
                    {
                        "step": 4,
                        "filename": filename,
                        "output_filename": output_filename,
                        "processed": processed,
                        "total": total,
                        "eta_seconds": _estimate_eta_seconds(completed_durations, total, processed),
                    },
                )

            if not generated_outputs:
                yield _sse(
                    "batch_done",
                    {
                        "download_url": "",
                        "filename": "",
                        "processed": processed,
                        "total": total,
                        "success_count": 0,
                        "failed_count": len(failures),
                        "failures": failures,
                    },
                )
                return

            batch_id = uuid.uuid4().hex
            batch_filename = f"formatted_cvs_{_kl_date_suffix()}.zip"
            zip_path = UPLOAD_DIR / f"{batch_id}_formatted.zip"
            _write_batch_zip(zip_path, generated_outputs, failures)
            BATCH_ZIP_FILENAME_MAP[batch_id] = batch_filename

            yield _sse(
                "batch_done",
                {
                    "download_url": f"/api/download-batch/{batch_id}",
                    "filename": batch_filename,
                    "processed": processed,
                    "total": total,
                    "success_count": len(generated_outputs),
                    "failed_count": len(failures),
                    "failures": failures,
                },
            )
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
        finally:
            for output in generated_outputs:
                Path(output["output_path"]).unlink(missing_ok=True)
            for path in upload_paths:
                path.unlink(missing_ok=True)

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

    filename = DOWNLOAD_FILENAME_MAP.pop(file_id, f"FirstName_LastName_OxyCVFormat_{_kl_date_suffix()}.docx")
    return FileResponse(
        path=str(output_path),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/api/download-batch/{batch_id}")
async def download_batch_file(batch_id: str):
    zip_path = UPLOAD_DIR / f"{batch_id}_formatted.zip"
    if not zip_path.exists():
        raise HTTPException(404, "Batch file not found or already downloaded.")

    filename = BATCH_ZIP_FILENAME_MAP.pop(batch_id, f"formatted_cvs_{_kl_date_suffix()}.zip")
    return FileResponse(
        path=str(zip_path),
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
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


def _is_render_environment() -> bool:
    return bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))


def _kl_date_suffix() -> str:
    return datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%d%b%y")


def _estimate_eta_seconds(completed_durations: list[float], total: int, processed: int) -> int | None:
    if processed < 1 or not completed_durations:
        return None
    remaining = max(total - processed, 0)
    avg_seconds = sum(completed_durations) / len(completed_durations)
    return int(round(avg_seconds * remaining))


def _write_batch_zip(zip_path: Path, outputs: list[dict[str, str]], failures: list[dict[str, str]]) -> None:
    used_names: set[str] = set()
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in outputs:
            arcname = _unique_archive_name(item["output_filename"], used_names)
            archive.write(item["output_path"], arcname=arcname)
        if failures:
            archive.writestr(
                "errors.json",
                json.dumps({"failed_files": failures}, indent=2),
            )


def _unique_archive_name(filename: str, used_names: set[str]) -> str:
    base = Path(filename).stem
    suffix = Path(filename).suffix or ".docx"
    candidate = f"{base}{suffix}"
    counter = 2
    while candidate in used_names:
        candidate = f"{base}_{counter}{suffix}"
        counter += 1
    used_names.add(candidate)
    return candidate


def _build_output_filename(full_name: str) -> str:
    date_suffix = _kl_date_suffix()
    cleaned_parts = [part for part in full_name.strip().split() if part]
    if not cleaned_parts:
        return f"FirstName_LastName_OxyCVFormat_{date_suffix}.docx"

    first_name = cleaned_parts[0]
    if len(cleaned_parts) == 1:
        return f"{first_name}_OxyCVFormat_{date_suffix}.docx"
    last_name = cleaned_parts[-1]
    return f"{first_name}_{last_name}_OxyCVFormat_{date_suffix}.docx"
