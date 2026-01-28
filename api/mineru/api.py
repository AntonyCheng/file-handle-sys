import os
import uuid
import threading
import time
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
import logging
import requests

from fastapi import APIRouter, File, UploadFile, HTTPException, BackgroundTasks, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import KK_HOST_PUBLIC, MAX_UPLOAD_SIZE_BYTES, MINERU_DEFAULT_BASE_URL, MINERU_TIMEOUT_SECONDS, MINERU_WORKER_THREADS, MINERU_PARSE_PATH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mineru", tags=["mineru"])

# temp dirs
UPLOAD_DIR = Path("temp/mineru/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# simple in-memory task store and queue (Mode2: hosted async)
_task_lock = threading.Lock()
_task_status: Dict[str, Dict[str, Any]] = {}
_task_queue = []
_queue_cv = threading.Condition()

# config defaults (can be later moved to config.py)
MINERU_TIMEOUT_SECONDS = MINERU_TIMEOUT_SECONDS
MINERU_WORKER_THREADS = MINERU_WORKER_THREADS
MINERU_PARSE_PATH = MINERU_PARSE_PATH


def _save_upload(file: UploadFile) -> Path:
    ext = Path(file.filename or "").suffix
    fid = str(uuid.uuid4())
    name = f"{fid}{ext}"
    dest = UPLOAD_DIR / name
    total = 0
    with open(dest, "wb") as out_f:
        while True:
            chunk = file.file.read(2 * 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_SIZE_BYTES:
                out_f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="file too large")
            out_f.write(chunk)
    return dest


def _normalize_mineru_response(data: Any) -> Any:
    """
    If Mineru response contains 'results' as a single-item mapping whose value
    contains 'md_content', set 'md_result' to that md_content string and remove 'results'.
    """
    try:
        if isinstance(data, dict) and "results" in data:
            results = data.get("results")
            # Case: results is a mapping with a single entry containing md_content
            if isinstance(results, dict) and len(results) == 1:
                first_val = next(iter(results.values()))
                if isinstance(first_val, dict) and "md_content" in first_val:
                    data["md_result"] = first_val["md_content"]
                    try:
                        del data["results"]
                    except Exception:
                        pass
            # Case: results already normalized as a string -> move to md_result
            elif isinstance(results, str):
                data["md_result"] = results
                try:
                    del data["results"]
                except Exception:
                    pass
    except Exception:
        # On any error, return original data unchanged
        return data
    return data


def _call_mineru_parse(base_url: str, file_path: Optional[Path] = None, target_url: Optional[str] = None, timeout: int = MINERU_TIMEOUT_SECONDS):
    """
    Call Mineru parse endpoint. Only file_path (multipart upload) is supported.
    URL-based parsing has been disabled intentionally.
    """
    if not base_url:
        raise RuntimeError("mineru base_url required")
    if not file_path:
        raise RuntimeError("URL-based parsing disabled; use file upload")
    url = f"{base_url.rstrip('/')}{MINERU_PARSE_PATH}"
    try:
        # Mineru OpenAPI expects multipart form field name "files" (array).
        with open(file_path, "rb") as f:
            files = [("files", (file_path.name, f, "application/octet-stream"))]
            resp = requests.post(url, files=files, timeout=timeout)
        resp.raise_for_status()
        # Try to return JSON, if response is not JSON, return text wrapped
        try:
            data = resp.json()
        except ValueError:
            data = {"text": resp.text}
        return _normalize_mineru_response(data)
    except Exception as e:
        logger.error(f"Mineru call failed: {e}")
        raise


def _worker_loop():
    while True:
        with _queue_cv:
            while not _task_queue:
                _queue_cv.wait()
            task_id = _task_queue.pop(0)
        # process task
        with _task_lock:
            task = _task_status.get(task_id)
            if not task or task.get("status") != "pending":
                continue
            task["status"] = "processing"
            task["started_at"] = time.time()
        try:
            base_url = task.get("base_url")
            file_path = task.get("file_path")
            result = None
            if file_path:
                result = _call_mineru_parse(base_url, file_path=Path(file_path))
            else:
                # URL-based tasks removed; mark task as error
                task["status"] = "error"
                task["error"] = "no file_path in task; URL-based parsing removed"
                task["finished_at"] = time.time()
                continue
            with _task_lock:
                task["status"] = "done"
                task["result"] = result
                task["finished_at"] = time.time()
        except Exception as e:
            with _task_lock:
                task["status"] = "error"
                task["error"] = str(e)
                task["finished_at"] = time.time()


# start worker threads
for i in range(MINERU_WORKER_THREADS):
    t = threading.Thread(target=_worker_loop, daemon=True, name=f"mineru-worker-{i}")
    t.start()




@router.post("/parse/file")
async def parse_file(file: UploadFile = File(...), base_url: Optional[str] = Form(None)):
    """
    Synchronous parse for an uploaded file. Provide optional form field base_url to override default.
    """
    # Enforce PDF-only uploads
    is_pdf = False
    if getattr(file, "content_type", None):
        if "pdf" in file.content_type.lower():
            is_pdf = True
    if not is_pdf and getattr(file, "filename", None):
        if file.filename.lower().endswith(".pdf"):
            is_pdf = True
    if not is_pdf:
        raise HTTPException(status_code=415, detail="Only PDF files are supported for Mineru parsing")

    saved = _save_upload(file)
    use_base = base_url or MINERU_DEFAULT_BASE_URL
    if not use_base:
        raise HTTPException(status_code=400, detail="MINERU_DEFAULT_BASE_URL not configured")
    try:
        res = _call_mineru_parse(use_base, file_path=saved)
        return JSONResponse(res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# URL-based parsing endpoints have been removed.


@router.post("/parse_async/file")
async def parse_async_file(file: UploadFile = File(...), base_url: Optional[str] = Form(None)):
    """
    Submit async parse job for uploaded file. Returns { task_id }.
    """
    # Enforce PDF-only uploads
    is_pdf = False
    if getattr(file, "content_type", None):
        if "pdf" in file.content_type.lower():
            is_pdf = True
    if not is_pdf and getattr(file, "filename", None):
        if file.filename.lower().endswith(".pdf"):
            is_pdf = True
    if not is_pdf:
        raise HTTPException(status_code=415, detail="Only PDF files are supported for Mineru parsing")

    saved = _save_upload(file)
    use_base = base_url or MINERU_DEFAULT_BASE_URL
    if not use_base:
        raise HTTPException(status_code=400, detail="MINERU_DEFAULT_BASE_URL not configured")
    task_id = str(uuid.uuid4())
    with _task_lock:
        _task_status[task_id] = {"status": "pending", "base_url": use_base, "file_path": str(saved), "created_at": time.time()}
    with _queue_cv:
        _task_queue.append(task_id)
        _queue_cv.notify()
    return JSONResponse({"task_id": task_id})


@router.get("/parse_result/{task_id}")
async def parse_result(task_id: str):
    with _task_lock:
        task = _task_status.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="unknown task_id")
        return JSONResponse(task)


