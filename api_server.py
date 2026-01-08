#!/usr/bin/env python3
"""
FastAPI文档转换服务
基于Gotenberg的Office文档转换为PDF的API服务
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import List
import logging

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import requests

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(
    title="文档转换API",
    description="基于Gotenberg的Office文档转换为PDF的API服务",
    version="1.0.0",
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 创建临时目录用于存储上传的文件
UPLOAD_DIR = Path("temp/libreoffice/uploads")
OUTPUT_DIR = Path("temp/libreoffice/outputs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 支持的文件格式
SUPPORTED_FORMATS = [
    '.doc', '.docx',      # Microsoft Word
    '.odt',               # OpenDocument Text
    '.rtf',               # Rich Text Format
    '.txt',               # 纯文本
    '.html', '.htm',      # HTML
    '.xml',               # XML
    '.xls', '.xlsx',      # Microsoft Excel
    '.ods',               # OpenDocument Spreadsheet
    '.csv',               # 逗号分隔值
    '.ppt', '.pptx',      # Microsoft PowerPoint
    '.odp',               # OpenDocument Presentation
]


# 不需要Pydantic模型，API直接使用Form参数

def cleanup_temp_files(file_paths: List[str]):
    """清理临时文件"""
    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"已清理临时文件: {file_path}")
        except Exception as e:
            logger.error(f"清理临时文件失败: {file_path}, 错误: {e}")

@app.post("/libre_office/converter_to_pdf")
async def convert_document(
    background_tasks: BackgroundTasks,
    base_url: str = Form(..., description="Gotenberg服务地址"),
    file: UploadFile = File(..., description="要转换的文档文件"),
    marginTop: str = Form(default="1", description="上边距（英寸）"),
    marginBottom: str = Form(default="1", description="下边距（英寸）"),
    marginLeft: str = Form(default="1", description="左边距（英寸）"),
    marginRight: str = Form(default="1", description="右边距（英寸）"),
    landscape: str = Form(default="false", description="是否横向打印"),
    pageRanges: str = Form(default="", description="页面范围（如：1-3,5）"),
    printBackground: str = Form(default="true", description="是否打印背景"),
    preferCSSPageSize: str = Form(default="true", description="优先使用CSS页面大小")
):
    """
    单文件转换
    将上传的Office文档转换为PDF，直接返回PDF文件
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    # 检查文件扩展名
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(SUPPORTED_FORMATS)}"
        )

    # 生成唯一文件ID
    file_id = str(uuid.uuid4())
    input_path = UPLOAD_DIR / f"{file_id}_{file.filename}"
    output_path = OUTPUT_DIR / f"{file_id}.pdf"

    temp_files = [str(input_path), str(output_path)]

    try:
        # 保存上传的文件
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"文件已保存: {input_path}")

        # 构建转换选项
        convert_options = {
            'marginTop': marginTop,
            'marginBottom': marginBottom,
            'marginLeft': marginLeft,
            'marginRight': marginRight,
            'landscape': landscape,
            'printBackground': printBackground,
            'preferCSSPageSize': preferCSSPageSize
        }

        # 添加页面范围（如果提供）
        if pageRanges.strip():
            convert_options['pageRanges'] = pageRanges

        # 调用Gotenberg API
        gotenberg_url = f"{base_url.rstrip('/')}/forms/libreoffice/convert"

        with open(input_path, 'rb') as f:
            files = {'file': f}
            response = requests.post(gotenberg_url, files=files, data=convert_options, timeout=300)

        if response.status_code == 200:
            # 保存PDF文件
            with open(output_path, 'wb') as f:
                f.write(response.content)

            # 计划清理临时文件（延迟执行）
            background_tasks.add_task(cleanup_temp_files, temp_files)

            # 直接返回PDF文件
            return FileResponse(
                path=str(output_path),
                media_type='application/pdf',
                filename=f"converted_{file.filename.rsplit('.', 1)[0]}.pdf"
            )
        else:
            # 清理失败的文件
            cleanup_temp_files(temp_files)
            logger.error(f"Gotenberg API错误: HTTP {response.status_code}, {response.text}")
            raise HTTPException(status_code=500, detail="文档转换失败")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"转换过程中发生错误: {e}")
        cleanup_temp_files(temp_files)
        raise HTTPException(status_code=500, detail=f"转换失败: {str(e)}")

def cleanup_temp_files(file_paths):
    """清理临时文件"""
    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"已清理临时文件: {file_path}")
        except Exception as e:
            logger.error(f"清理临时文件失败: {file_path}, 错误: {e}")

if __name__ == "__main__":
    # 启动服务器
    print("启动FastAPI文档转换服务器...")
    print("访问地址: http://localhost:8000")
    print("API文档: http://localhost:8000/docs")
    print("按 Ctrl+C 停止服务器")

    try:
        uvicorn.run(
            "api_server:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n服务器已停止")