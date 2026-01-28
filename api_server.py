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
from api.libreoffice import api as libreoffice_api
from api.kkfileview import api as kkfileview_api

# 挂载 libreoffice 子路由
app.include_router(libreoffice_api.router)
# 挂载 kkfileview 子路由
app.include_router(kkfileview_api.router)

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