import os
import shutil
import uuid
import threading
from pathlib import Path
from typing import List, Dict, Any
import logging

import requests
from fastapi import APIRouter, File, UploadFile, HTTPException, BackgroundTasks, Form
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/libre_office", tags=["libre_office"])

# 目录配置
UPLOAD_DIR = Path("temp/libreoffice/uploads")
OUTPUT_DIR = Path("temp/libreoffice/outputs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_FORMATS = [
    '.doc', '.docx',
    '.odt',
    '.rtf',
    '.txt',
    '.html', '.htm',
    '.xml',
    '.xls', '.xlsx',
    '.ods',
    '.csv',
    '.ppt', '.pptx',
    '.odp',
]


def cleanup_temp_files(file_paths: List[str]):
    """清理临时文件"""
    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"已清理临时文件: {file_path}")
        except Exception as e:
            logger.error(f"清理临时文件失败: {file_path}, 错误: {e}")


# 简单的内存任务状态存储（单进程场景）
_task_lock = threading.Lock()
_task_status: Dict[str, Dict[str, Any]] = {}


def _post_to_gotenberg_and_save(gotenberg_url: str, input_path: Path, output_path: Path, convert_options: Dict[str, str], timeout: int = 300):
    with open(input_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(gotenberg_url, files=files, data=convert_options, timeout=timeout)

    if response.status_code == 200:
        with open(output_path, 'wb') as out_f:
            out_f.write(response.content)
        return True, None
    else:
        return False, f"HTTP {response.status_code}: {response.text}"


@router.post("/converter_to_pdf")
async def convert_document(
    background_tasks: BackgroundTasks,
    base_url: str = Form(..., description="Gotenberg服务地址"),
    file: UploadFile = File(..., description="要转换的文档文件"),
    marginTop: str = Form(default="1"),
    marginBottom: str = Form(default="1"),
    marginLeft: str = Form(default="1"),
    marginRight: str = Form(default="1"),
    landscape: str = Form(default="false"),
    pageRanges: str = Form(default=""),
    printBackground: str = Form(default="true"),
    preferCSSPageSize: str = Form(default="true")
):
    """
    同步转换接口：上传文件并直接返回 PDF 文件，转换完成后立即调度清理临时文件。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file_ext}")

    file_id = str(uuid.uuid4())
    input_path = UPLOAD_DIR / f"{file_id}_{file.filename}"
    output_path = OUTPUT_DIR / f"{file_id}.pdf"

    temp_files = [str(input_path), str(output_path)]

    try:
        # 保存上传文件
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        convert_options = {
            'marginTop': marginTop,
            'marginBottom': marginBottom,
            'marginLeft': marginLeft,
            'marginRight': marginRight,
            'landscape': landscape,
            'printBackground': printBackground,
            'preferCSSPageSize': preferCSSPageSize
        }
        if pageRanges.strip():
            convert_options['pageRanges'] = pageRanges

        gotenberg_url = f"{base_url.rstrip('/')}/forms/libreoffice/convert"

        success, err = _post_to_gotenberg_and_save(gotenberg_url, input_path, output_path, convert_options)
        if success:
            # 清理临时文件（在响应完成后执行）
            background_tasks.add_task(cleanup_temp_files, temp_files)
            return FileResponse(
                path=str(output_path),
                media_type='application/pdf',
                filename=f"converted_{file.filename.rsplit('.', 1)[0]}.pdf"
            )
        else:
            cleanup_temp_files(temp_files)
            logger.error(f"Gotenberg API错误: {err}")
            raise HTTPException(status_code=500, detail="文档转换失败")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"转换过程中发生错误: {e}")
        cleanup_temp_files(temp_files)
        raise HTTPException(status_code=500, detail=f"转换失败: {str(e)}")


def _async_convert_task(task_id: str, base_url: str, input_path: str, output_path: str, convert_options: Dict[str, str]):
    try:
        gotenberg_url = f"{base_url.rstrip('/')}/forms/libreoffice/convert"
        success, err = _post_to_gotenberg_and_save(gotenberg_url, Path(input_path), Path(output_path), convert_options)
        with _task_lock:
            if success:
                _task_status[task_id]['status'] = 'done'
                _task_status[task_id]['output_path'] = output_path
            else:
                _task_status[task_id]['status'] = 'error'
                _task_status[task_id]['error'] = err
    except Exception as e:
        logger.error(f"异步任务异常: {e}")
        with _task_lock:
            _task_status[task_id]['status'] = 'error'
            _task_status[task_id]['error'] = str(e)


@router.post("/converter_to_pdf_async")
async def convert_document_async(
    background_tasks: BackgroundTasks,
    base_url: str = Form(..., description="Gotenberg服务地址"),
    file: UploadFile = File(..., description="要转换的文档文件"),
    marginTop: str = Form(default="1"),
    marginBottom: str = Form(default="1"),
    marginLeft: str = Form(default="1"),
    marginRight: str = Form(default="1"),
    landscape: str = Form(default="false"),
    pageRanges: str = Form(default=""),
    printBackground: str = Form(default="true"),
    preferCSSPageSize: str = Form(default="true")
):
    """
    异步接口：返回 task_id，转换在后台执行。使用 /converter_result/{task_id} 获取结果。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file_ext}")

    task_id = str(uuid.uuid4())
    input_path = UPLOAD_DIR / f"{task_id}_{file.filename}"
    output_path = OUTPUT_DIR / f"{task_id}.pdf"

    try:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        convert_options = {
            'marginTop': marginTop,
            'marginBottom': marginBottom,
            'marginLeft': marginLeft,
            'marginRight': marginRight,
            'landscape': landscape,
            'printBackground': printBackground,
            'preferCSSPageSize': preferCSSPageSize
        }
        if pageRanges.strip():
            convert_options['pageRanges'] = pageRanges

        with _task_lock:
            _task_status[task_id] = {'status': 'pending', 'output_path': None, 'error': None}

        # 使用后台线程立即开始异步任务（也可以使用 background_tasks.add_task，这里用线程以便尽快启动）
        thread = threading.Thread(target=_async_convert_task, args=(task_id, base_url, str(input_path), str(output_path), convert_options), daemon=True)
        thread.start()

        return JSONResponse({"task_id": task_id})

    except Exception as e:
        logger.error(f"异步转换提交失败: {e}")
        if os.path.exists(input_path):
            os.remove(input_path)
        raise HTTPException(status_code=500, detail=f"异步转换提交失败: {str(e)}")


@router.get("/converter_result/{task_id}")
async def get_conversion_result(task_id: str, background_tasks: BackgroundTasks):
    with _task_lock:
        if task_id not in _task_status:
            raise HTTPException(status_code=404, detail="未知的 task_id")
        status = _task_status[task_id].get('status')
        output_path = _task_status[task_id].get('output_path')
        error = _task_status[task_id].get('error')

    if status == 'pending':
        return JSONResponse({"status": "pending"})
    elif status == 'error':
        return JSONResponse({"status": "error", "error": error}, status_code=500)
    elif status == 'done' and output_path and os.path.exists(output_path):
        # 在响应后清理相关 temp 文件
        input_glob = str(UPLOAD_DIR / f"{task_id}_*")
        related_inputs = list(Path(UPLOAD_DIR).glob(f"{task_id}_*"))
        temp_files = [str(p) for p in related_inputs] + [str(output_path)]
        background_tasks.add_task(cleanup_temp_files, temp_files)
        return FileResponse(path=output_path, media_type='application/pdf', filename=f"converted_{task_id}.pdf")
    else:
        return JSONResponse({"status": "unknown"}, status_code=500)


