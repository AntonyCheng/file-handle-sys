import os
import shutil
import uuid
import threading
import time
import base64
import urllib.parse
from pathlib import Path
from typing import List, Optional
import logging

from fastapi import APIRouter, File, UploadFile, HTTPException, BackgroundTasks, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from config import KK_HOST_PUBLIC, MAX_UPLOAD_SIZE_BYTES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kkfileview", tags=["kkfileview"])

# Config (read from config.py which loads env/.env/env.example)

# Temp dirs
UPLOAD_DIR = Path("temp/kkfileview/uploads")
OUTPUT_DIR = Path("temp/kkfileview/outputs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_temp_files(file_paths: List[str]):
    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"已清理临时文件: {file_path}")
        except Exception as e:
            logger.error(f"清理临时文件失败: {file_path}, 错误: {e}")


def _schedule_cleanup(paths: List[str], delay: int):
    try:
        timer = threading.Timer(delay, cleanup_temp_files, args=(paths,))
        timer.daemon = True
        timer.start()
    except Exception as e:
        logger.error(f"无法调度清理任务: {e}")


class PreviewURLBody(BaseModel):
    kk_base_url: str
    target_url: str


@router.post("/preview/url")
async def preview_url(body: PreviewURLBody):
    """
    通过已存在的文件 URL 生成 kkFileView preview 链接（不下载）。
    返回 JSON { preview_url }。
    """
    kk_base_url = body.kk_base_url
    target_url = body.target_url
    if not kk_base_url:
        raise HTTPException(status_code=400, detail="缺少 kk_base_url")
    parsed = urllib.parse.urlparse(target_url)
    path = parsed.path or ""
    suffix = Path(path).suffix
    # 如果 path 没有扩展名，允许在 query 中带 fullfilename=xxx.ext
    if not suffix:
        qs = urllib.parse.parse_qs(parsed.query)
        fullnames = qs.get("fullfilename") or qs.get("fullname") or qs.get("filename")
        if fullnames:
            candidate = fullnames[0]
            if Path(candidate).suffix:
                # accept target_url as-is (kk expects fullfilename in query)
                original_url = target_url
            else:
                raise HTTPException(status_code=400, detail="query 参数 fullfilename 必须包含扩展名（如 fullfilename=file.pdf）")
        else:
            raise HTTPException(status_code=400, detail="target_url 必须以文件名和扩展名结尾，或在 query 中包含 fullfilename=xxx.ext")
    else:
        original_url = target_url
    b64 = base64.b64encode(original_url.encode()).decode()
    encoded = urllib.parse.quote(b64, safe='')
    preview_url = f"{kk_base_url.rstrip('/')}/onlinePreview?url={encoded}"
    return JSONResponse({"preview_url": preview_url})


@router.post("/preview/file")
async def preview_file(
    background_tasks: BackgroundTasks,
    kk_base_url: str = Form(..., description="kkFileView 服务地址，例如 http://127.0.0.1:8012"),
    file: UploadFile = File(..., description="要上传并临时托管的文件"),
):
    """
    上传文件并临时托管，然后生成 kkFileView preview 链接。
    返回 JSON { preview_url, temp_url }。
    """
    if not kk_base_url:
        raise HTTPException(status_code=400, detail="缺少 kk_base_url")

    # Save upload with size check
    file_ext = Path(file.filename).suffix if file.filename else ""
    file_id = str(uuid.uuid4())
    safe_name = f"{file_id}{file_ext}"
    dest_path = UPLOAD_DIR / safe_name

    try:
        total = 0
        with open(dest_path, "wb") as out_f:
            while True:
                chunk = await file.read(2 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE_BYTES:
                    out_f.close()
                    os.remove(dest_path)
                    raise HTTPException(status_code=413, detail=f"文件大小超过限制: {MAX_UPLOAD_SIZE_BYTES} bytes")
                out_f.write(chunk)

        logger.info(f"已保存上传文件: {dest_path} (size={total})")

        # build temp URL (accessible by kk). Use KK_HOST_PUBLIC as host for constructing URL (no sig)
        host_cfg = KK_HOST_PUBLIC.rstrip('/')
        if host_cfg.startswith("http://") or host_cfg.startswith("https://"):
            base_url = host_cfg
        else:
            base_url = f"http://{host_cfg}"
        # Use uploaded original filename so kkFileView can detect type
        original_name = file.filename or safe_name
        quoted_name = urllib.parse.quote(original_name)
        temp_url = f"{base_url}/kkfileview/temp/{file_id}?fullfilename={quoted_name}"

        # base64 + urlencode for kk (kk 3.x)
        b64 = base64.b64encode(temp_url.encode()).decode()
        encoded = urllib.parse.quote(b64, safe='')
        preview_url = f"{kk_base_url.rstrip('/')}/onlinePreview?url={encoded}"

        # keep file permanently (no scheduled cleanup)
        return JSONResponse({"preview_url": preview_url, "temp_url": temp_url})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传处理失败: {e}")
        if dest_path.exists():
            os.remove(dest_path)
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


@router.get("/temp/{file_id}")
async def temp_file(file_id: str, fullfilename: Optional[str] = None):
    """
    临时文件访问：直接返回文件流（无签名/过期校验，按用户要求简单暴露）。
    支持 `fullfilename` 查询参数以让客户端（如 kkFileView）识别文件类型。
    """
    candidates = list(UPLOAD_DIR.glob(f"{file_id}*"))
    if not candidates:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = candidates[0]
    resp_filename = fullfilename or path.name
    return FileResponse(path=str(path), filename=resp_filename)
# Note: auth endpoint and signature logic removed per user instruction.





