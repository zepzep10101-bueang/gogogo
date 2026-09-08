from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import json
import os
import uvicorn
import asyncio
import copy
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

# [디오 최종 방어막 수정: 0.5초 기다렸다가 변수를 읽어오게 해서 Empty host 원천 차단!]
import time
time.sleep(0.5)

collection = None
MONGO_URI = os.environ.get("MONGO_URI", "").strip()

try:
    if MONGO_URI:
        client = MongoClient(
            MONGO_URI,
            maxPoolSize=50, 
            waitQueueTimeoutMS=5000, 
            socketTimeoutMS=30000, 
            serverSelectionTimeoutMS=3000, 
            retryWrites=True
        )
        db = client["dashboard_db"]
        collection = db["dashboard_data"]
        client.admin.command('ping')
    else:
        print("경고: MONGO_URI 환경 변수가 비어 있습니다.")
except Exception as e:
    print("망고로드 첫 연결 실패 (나중에 다시 시도됨):", e)
