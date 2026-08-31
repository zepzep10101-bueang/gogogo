from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import json
import os
import uvicorn
import asyncio
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

def load_data():
    initial_data = {
        "_id": "main_state",
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": 0} for i in range(16)],
        "chat_history": [],
        "global_notice": "📌 다 함께 모여서 열심히 마감해 봅시다!",
        "attendance": {},
        "admin_log": [],
        "trackers": {}
    }
    try:
        if collection is not None:
            data = collection.find_one({"_id": "main_state"})
            if data:
                cards = data.get("cards", [])
                if len(cards) > 16:
                    data["cards"] = cards[:16]
                elif len(cards) < 16:
                    new_cards = [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": 0} for i in range(len(cards), 16)]
                    data["cards"].extend(new_cards)
                for i, card in enumerate(data["cards"]):
                    card["user"] = card.get("user") or f"자리{i+1}"
                    card["is_mosaic"] = card.get("is_mosaic", False)
                    card["is_large"] = card.get("is_large", False)
                    card["status"] = card.get("status", 0)
                if "global_notice" not in data:
                    data["global_notice"] = "📌 다 함께 모여서 열심히 마감해 봅시다!"
                if "attendance" not in data:
                    data["attendance"] = {}
                if "admin_log" not in data:
                    data["admin_log"] = []
                if "trackers" not in data:
                    data["trackers"] = {}
                return data
            else:
                collection.update_one({"_id": "main_state"}, {"$set": initial_data}, upsert=True)
                return initial_data
        else:
            return initial_data
    except Exception as e:
        print("망고로드 초기화 에러 (하지만 서버는 죽지 않습니다!):", e)
        return initial_data

def save_data(data):
    try:
        if collection is not None:
            collection.update_one({"_id": "main_state"}, {"$set": data}, upsert=True)
    except Exception as e:
        print("망고로드 저장 에러:", e)

server_state = load_data()
app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections = []
        self.active_shares = {}
        self.active_users = {}
        self.active_slots = {} 

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.active_users[websocket] = "연결중..."

    def disconnect(self, websocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if websocket in self.active_users:
            del self.active_users[websocket]
            
        disconnected_client = str(id(websocket))
        freed_indexes = []
        to_remove = [idx for idx, cid in self.active_shares.items() if cid == disconnected_client]
        for idx in to_remove:
            del self.active_shares[idx]
            freed_indexes.append(idx)
        return freed_indexes

    async def broadcast(self, message, exclude=None):
        async def send_to_client(conn):
            if conn != exclude:
                try:
                    await conn.send_text(message)
                except Exception:
                    self.disconnect(conn)

        tasks = [asyncio.create_task(send_to_client(conn)) for conn in list(self.active_connections)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_user_list(self):
        users_info = [{"clientId": str(id(ws)), "nickname": name} for ws, name in self.active_users.items() if name != "연결중..."]
        msg = json.dumps({"type": "user_list", "count": len(self.active_connections), "users": users_info})
        
        async def send_to_client(conn):
            try:
                await conn.send_text(msg)
            except Exception:
                self.disconnect(conn)

        tasks = [asyncio.create_task(send_to_client(conn)) for conn in list(self.active_connections)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

manager = ConnectionManager()

@app.get("/user_count")
def get_user_count():
    return {"count": len(manager.active_connections)}

@app.get("/", response_class=HTMLResponse)
def read_root():
    return r"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>🍀심사 합격 & 돈 긁어모으는 방🏆</title>
        <style>
            :root {
                --rec-primary: #d87093;
                --rec-bg: #ffffff;
                --rec-box: #ffffff;
                --rec-sec: #fff5f8;
                --rec-border: #ffb6c1;
                --rec-done: #ffe4e1;
            }
            
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Arial', sans-serif; }
            body, html { width: 100%; height: 100%; overflow-x: hidden; overflow-y: scroll; background: #111; touch-action: pan-y; }
            body::-webkit-scrollbar { width: 8px; }
            body::-webkit-scrollbar-track { background: #111; }
            body::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }
            body::-webkit-scrollbar-thumb:hover { background: #666; }
            .login-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #111; z-index: 9999; display: flex; align-items: center; justify-content: center; flex-direction: column; }
            .login-box { background: rgba(30, 30, 40, 0.9); padding: 30px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.2); text-align: center; color: white; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
            .login-box input { padding: 10px; margin-top: 10px; border-radius: 5px; border: none; width: 220px; text-align: center; }
            .login-box button { padding: 10px 20px; margin-top: 15px; border: none; border-radius: 5px; background: #ff7675; color: white; cursor: pointer; font-weight: bold; width: 100%; }
            .video-background { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; overflow: hidden; pointer-events: none; background: #000; }
            #bgMediaWrapper { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; }
            #bgMediaWrapper img, #bgMediaWrapper iframe { width: 100vw; height: 100vh; object-fit: cover; display: block; border: none; pointer-events: none; }
            .overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.05); z-index: 1; pointer-events: none; }
            
            .main-container { display: grid; grid-template-columns: minmax(0, 1fr) 240px; gap: 15px; padding: 15px; min-height: 100vh; color: white; position: relative; z-index: 2; align-items: start; max-width: 1800px; margin: 0 auto; transition: grid-template-columns 0.3s ease; }
            
            .card-grid { display: grid; gap: 10px; grid-template-columns: repeat(4, minmax(0, 1fr)); grid-auto-flow: dense; width: 100%; align-content: start; }
            @media (max-width: 1300px) { .card-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
            @media (max-width: 950px) { .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
            @media (max-width: 600px) { .card-grid { grid-template-columns: repeat(1, minmax(0, 1fr)); } }
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); min-height: 250px; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: all 0.3s ease; }
            .card-large { grid-column: span 2; grid-row: span 2; min-height: 510px; }
            .card-header { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 20; width: 100%; }
            .btn-group { display: flex; gap: 2px; width: 100%; justify-content: center; flex-wrap: nowrap; overflow: visible; }
            .share-btn { padding: 4px 2px; font-size: 10px; color: white; border: none; border-radius: 3px; cursor: pointer; white-space: nowrap; font-weight: bold; text-align: center; flex-grow: 1; transition: opacity 0.2s; }
            .share-btn:hover { opacity: 0.8; }
            .card-stream-box { width: 100%; flex-grow: 1; min-height: 135px; background: rgba(0, 0, 0, 0.15); border-radius: 8px; overflow: hidden; position: relative; margin-top: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 2; pointer-events: none; transition: visibility 0.2s; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: transparent; position: absolute; top: 0; left: 0; z-index: 10; transition: filter 0.2s ease-in-out; pointer-events: auto; }
            .side-panel { display: flex; flex-direction: column; gap: 15px; position: sticky; top: 15px; height: calc(100vh - 30px); min-width: 0; }
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 12px; border: 1px solid rgba(255, 255, 255, 0.2); min-width: 0; word-break: keep-all; overflow-wrap: break-word; }
            .settings-toggle-btn { border: none; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: bold; white-space: nowrap; }
            .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0, 0, 0, 0.7); z-index: 10000; align-items: center; justify-content: center; }
            .modal-box { background: rgba(30, 30, 40, 0.95); border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 12px; padding: 20px; width: 350px; max-height: 80vh; overflow-y: auto; color: white; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
            .close-btn { position: absolute; top: 12px; right: 15px; background: transparent; border: none; color: white; font-size: 14px; cursor: pointer; font-weight: bold; transition: 0.2s; z-index: 100;}
            .close-btn:hover { color: #ff7675; }
            .chat-box { display: flex; flex-direction: column; flex-grow: 1; min-height: 0; height: 100%; }
            #chatHistory { flex-grow: 1; overflow-y: auto; margin-top: 8px; font-size: 13px; color: #ddd; line-height: 1.5; word-break: break-all; padding-right: 4px; }
            .chat-input { display: flex; margin-top: 8px; gap: 4px; }
            .chat-input input { flex-grow: 1; padding: 7px; border-radius: 4px; border: none; background: rgba(255, 255, 255, 0.9); color: black; min-width: 0; font-size: 12px; }
            .chat-input button { padding: 7px 10px; background: #ff7675; border: none; color: white; border-radius: 4px; cursor: pointer; flex-shrink: 0; font-size: 12px; }
            .status-indicator { font-size: 11px; padding: 2px 6px; border-radius: 3px; display: inline-block; margin-left: 5px; }
            .status-online { background: #00b894; color: white; }
            .status-offline { background: #d63031; color: white; }
            .recovery-btn { background: #d63031; color: white; border: none; border-radius: 4px; padding: 3px 6px; font-size: 10px; cursor: pointer; font-weight: bold; white-space: nowrap; }
            
            .calendar-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; margin-bottom: 4px; }
            .cal-day { background: rgba(0,0,0,0.5); padding: 5px 0; text-align: center; border-radius: 3px; font-size: 11px; font-weight: bold; }
            .cal-day.today { border: 1px solid #ff7675; cursor: pointer; background: rgba(255, 118, 117, 0.2); }
            .cal-day.today:hover { background: rgba(255, 118, 117, 0.5); }
            .cal-day.stamped { background: rgba(39, 174, 96, 0.4); border: none; cursor: default; }
            
            .rec-container { background-color: var(--rec-bg); font-family: 'Malgun Gothic', sans-serif; color: #4a4a4a; padding: 15px; width: 100%; border-radius: 10px; box-sizing: border-box; }
            .rec-header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; flex-wrap: wrap; gap: 10px; border-bottom: 2px solid var(--rec-primary); padding-bottom: 10px; padding-right: 35px; }
            .rec-header-bar h1 { margin: 0; color: var(--rec-primary); font-size: 20px; font-weight: bold; }
            .rec-color-picker-box { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: bold; color: var(--rec-primary); }
            .rec-color-picker-box input[type="color"] { width: 25px; height: 25px; border: none; border-radius: 50%; cursor: pointer; padding: 0; background: none; }
            
            .rec-section { margin-bottom: 15px; padding: 15px; background: var(--rec-sec); border-radius: 10px; border: 1px solid var(--rec-border); display: flex; flex-direction: column; gap: 12px; }
            .rec-section h3 { margin: 0; color: var(--rec-primary); font-size: 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; padding-bottom: 10px; border-bottom: 1px dashed var(--rec-border); }
            
            .rec-record-box { display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: 15px; width: 100%; }
            .rec-timer-container { text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 6px; min-width: 140px; }
            .rec-timer-display { font-size: 26px; font-weight: bold; color: var(--rec-primary); line-height: 1; }
            .rec-input-edit { width: 70px; padding: 4px; border: 1px solid var(--rec-border); border-radius: 4px; font-size: 14px; text-align: right; background: #fff; color: #333; }
            
            .rec-banner { display: none; margin-top: 10px; padding: 8px; background: #fffacd; border: 1px solid #ffd700; border-radius: 5px; text-align: center; font-weight: bold; color: #b8860b; font-size: 13px; }
            .rec-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
            .rec-list li { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
            .rec-completed { text-decoration: line-through; color: #aaa; }
            .rec-time-tag { font-size: 12px; color: #888; margin-left: 10px; }
            
            .rec-todo-input-box { display: flex; gap: 5px; width: 100%; margin-top: 5px; }
            .rec-todo-input-box input { flex: 1; padding: 8px; border: 1px solid var(--rec-border); border-radius: 5px; font-size: 13px; background: #fff; color: #333; }
            
            .rec-btn { background: var(--rec-border); border: none; padding: 6px 12px; border-radius: 5px; cursor: pointer; font-weight: bold; color: #4a4a4a; font-size: 12px; white-space: nowrap; }
            .rec-btn:hover { opacity: 0.8; }
            .rec-btn-del { background: #ff9999; color: #fff; }
            
            .rec-cal-wrap { display: none; margin-top: 10px; }
            .rec-cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; text-align: center; font-size: 11px; }
            .rec-cal-header { font-weight: bold; color: var(--rec-primary); font-size: 12px; padding-bottom: 5px; }
            .rec-cal-day { background: #fff; border: 1px solid var(--rec-border); border-radius: 4px; padding: 3px 1px; min-height: 50px; display: flex; flex-direction: column; justify-content: flex-start; overflow: hidden; color: #333;}
            .rec-cal-day.done { background: var(--rec-done); }
            .rec-cal-day .date-num { font-weight: bold; font-size: 10px; margin-bottom: 2px; }
            .rec-cal-day .goal { font-size: 8.5px; color: #666; white-space: nowrap; }
            .rec-cal-day .act { font-size: 8.5px; font-weight: bold; color: var(--rec-primary); white-space: nowrap; }
            .rec-cal-day .time { font-size: 8.5px; color: #555; white-space: nowrap; margin-top: 1px; }
        </style>
    </head>
    <body>
        <script>
            document.addEventListener('contextmenu', function(e) { e.preventDefault(); });
            document.addEventListener('keydown', function(e) {
                if (e.keyCode === 123 || (e.ctrlKey && e.shiftKey && (e.keyCode === 73 || e.keyCode === 74 || e.keyCode === 67)) || (e.ctrlKey && e.keyCode === 85)) { e.preventDefault(); return false; }
            });
            document.addEventListener('wheel', function(e) { if (e.ctrlKey) { e.preventDefault(); } }, { passive: false });
            document.addEventListener('touchstart', function(e) { if (e.touches.length > 1) { e.preventDefault(); } }, { passive: false });
        </script>
        
        <div class="login-overlay" id="loginOverlay">
            <div class="login-box">
                <h2>🔒 행운방 입장</h2>
                <p style="font-size: 13px; color: #aaa; margin-top: 5px; margin-bottom: 10px;">닉네임은 한 번만 적으면 저장 돼!</p>
                <div id="loginUserCount" style="margin-bottom: 15px; font-size: 14px; font-weight: bold; color: #00b894; background: rgba(0, 184, 148, 0.15); padding: 8px; border-radius: 6px; border: 1px solid rgba(0, 184, 148, 0.4);">🔥 현재 달리고 있는 작가님: 확인 중...</div>
                <input type="text" id="nickInput" placeholder="내 닉네임 (예: 부엉)" onkeypress="if(event.key==='Enter') login()"><br>
                <input type="password" id="pwInput" placeholder="비밀번호" onkeypress="if(event.key==='Enter') login()"><br>
                <button onclick="login()">입장하기</button>
            </div>
        </div>
        
        <div id="noticeModal" class="modal-overlay" onclick="if(event.target===this) closeModal('noticeModal')">
            <div class="modal-box">
                <button class="close-btn" onclick="closeModal('noticeModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px;">📢 공지사항</h3>
                <div style="text-align: right; margin-bottom: 10px;">
                    <button onclick="addNotice()" style="background:#0984e3; color:white; border:none; border-radius:3px; padding:4px 8px; font-size:10px; cursor:pointer; font-weight:bold; margin-right: 3px;">➕ 추가</button>
                    <button onclick="editNotice()" style="background:#ff7675; color:white; border:none; border-radius:3px; padding:4px 8px; font-size:10px; cursor:pointer; font-weight:bold;">✏️ 수정/삭제</button>
                </div>
                <div id="noticeText" style="font-size: 13px; color: #fff; line-height: 1.6; word-break: break-all; background: rgba(0,0,0,0.3); padding: 10px; border-radius: 6px;">공지사항 로딩 중...</div>
            </div>
        </div>
        
        <div id="attendanceModal" class="modal-overlay" onclick="if(event.target===this) closeModal('attendanceModal')">
            <div class="modal-box" style="width: 380px;">
                <button class="close-btn" onclick="closeModal('attendanceModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px; text-align: center;">🏆 출석 현황</h3>
                <div style="background: rgba(0,0,0,0.4); padding: 12px; border-radius: 8px; margin-bottom: 12px;">
                    <div style="font-size: 13px; font-weight: bold; color: #ffeaa7; margin-bottom: 8px; text-align: center;" id="calMonthTitle">🍀 내 출석부</div>
                    <div class="calendar-grid" id="calendarGrid"></div>
                    <div style="font-size: 10px; color: #aaa; text-align: center; margin-top: 6px;">빨간 테두리(오늘)를 눌러서 도장을 찍어봐!</div>
                </div>
                <div style="background: rgba(0,0,0,0.4); padding: 12px; border-radius: 8px; margin-bottom: 12px;">
                    <div style="font-size: 13px; font-weight: bold; color: #ffeaa7; margin-bottom: 8px;" id="rankTitle">🏆 이번 달 출석 랭킹</div>
                    <div id="attRankingList" style="font-size: 12px; color: #ddd; line-height: 1.6; max-height: 100px; overflow-y: auto;">랭킹 로딩 중...</div>
                </div>
                <div style="background: rgba(0,0,0,0.4); padding: 12px; border-radius: 8px;">
                    <div style="font-size: 13px; font-weight: bold; color: #81ecec; margin-bottom: 8px;">✅ 오늘 출석한 사람</div>
                    <div id="attTodayList" style="font-size: 12px; color: #ddd; line-height: 1.6; display: flex; flex-wrap: wrap; gap: 4px;">대기 중...</div>
                </div>
            </div>
        </div>
        
        <div id="settingsModal" class="modal-overlay" onclick="if(event.target===this) closeModal('settingsModal')">
            <div class="modal-box">
                <button class="close-btn" onclick="closeModal('settingsModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px;">🖼️ 나만의 배경 (나에게만 보여요)</h3>
                <div>
                    <div style="font-size: 11px; color: #ccc; margin-bottom: 5px;">일반 사진 (움짤X):</div>
                    <input type="file" id="bgFileInput" accept="image/jpeg, image/png, image/webp" style="font-size:11px; width:100%; margin-bottom: 15px;" onchange="setLocalBackground(event)">
                    <div style="font-size: 11px; color: #ccc; margin-bottom: 5px;">유튜브 링크:</div>
                    <div style="display:flex; gap:5px;">
                        <input type="text" id="bgYoutubeInput" placeholder="URL 입력" style="flex-grow:1; font-size:11px; padding:5px; background:rgba(255,255,255,0.9); color:black; border:none; border-radius:3px; min-width:0;">
                        <button onclick="setYoutubeBackground()" style="font-size:11px; padding:5px 10px; background:#ff7675; border:none; color:white; border-radius:3px; cursor:pointer;">적용</button>
                    </div>
                </div>
            </div>
        </div>
        
        <div id="adminLogModal" class="modal-overlay" onclick="if(event.target===this) closeModal('adminLogModal')">
            <div class="modal-box" style="width: 350px;">
                <button class="close-btn" onclick="closeModal('adminLogModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px; color: #ff7675;">👑 비밀 출입 기록</h3>
                <div id="adminLogContent" style="background: rgba(0,0,0,0.5); padding: 10px; border-radius: 8px; height: 250px; overflow-y: auto; font-size: 12px; color: #ddd; line-height: 1.6;">기록이 없습니다.</div>
            </div>
        </div>

        <div id="recordModal" class="modal-overlay" onclick="if(event.target===this) closeModal('recordModal')">
            <div class="modal-box" style="width: 650px; max-width: 95vw; padding: 0; border: 2px solid var(--rec-border); background: #ffffff; overflow-x: hidden;">
                <button class="close-btn" onclick="closeModal('recordModal')" style="color: var(--rec-primary); text-shadow: 0 0 3px #fff; top: 18px;">❌</button>
                
                <div class="rec-container">
                    <div class="rec-header-bar">
                        <h1 id="rec-title">✨ 집필 기록방 ✨</h1>
                        <div class="rec-color-picker-box">
                            <div>
                                <label for="themeColorPicker">🎨 테마 색상:</label>
                                <input type="color" id="themeColorPicker" value="#d87093" oninput="changeThemeColor(this.value)" onchange="saveRecordData(true)">
                        </div>
                    </div>

                    <div class="rec-section">
                        <h3>
                            ⏱️ 오늘의 집필 기록
                            <button id="rec-save-btn" class="rec-btn" onclick="saveRecordData()" style="background: var(--rec-primary); color: #fff;">💾 기록 저장하기</button>
                        </h3>
                        <div class="rec-record-box">
                            <div style="display: flex; flex-direction: column; gap: 8px;">
                                <div>목표: <input type="number" id="rec-target-chars" class="rec-input-edit" oninput="checkGoalAchievement()">자</div>
                                <div>완료: <input type="number" id="rec-done-chars" class="rec-input-edit" oninput="checkGoalAchievement()">자</div>
                            </div>
                            <div class="rec-timer-container" id="rec-my-timer-area">
                                <div class="rec-timer-display" id="rec-main-timer">00:00:00</div>
                                <div style="display: flex; gap: 5px;">
                                    <button class="rec-btn" onclick="startMainTimer()">시작</button>
                                    <button class="rec-btn" onclick="pauseMainTimer()" style="background: #dda7a7; color:#fff;">정지</button>
                                    <button class="rec-btn" onclick="resetMainTimer()">리셋</button>
                                </div>
                            </div>
                        </div>
                        <div id="rec-congrats-banner" class="rec-banner">🎉 축하합니다! 오늘의 목표 분량을 모두 달성했습니다!</div>
                    </div>

                    <div class="rec-section" id="rec-todo-section">
                        <h3>📝 오늘의 할 일</h3>
                        <ul class="rec-list" id="rec-todo-list"></ul>
                        <div class="rec-todo-input-box" id="rec-todo-input-area">
                            <input type="text" id="rec-new-todo-text" placeholder="새로운 할 일을 입력하세요..." onkeypress="if(event.key==='Enter') addRecTodo()">
                            <button class="rec-btn" onclick="addRecTodo()">추가</button>
                        </div>
                    </div>

                    <div class="rec-section" id="rec-pomo-section">
                        <h3>
                            🍅 개인 뽀모도로
                        </h3>
                        <div style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between;">
                            <div style="display: flex; align-items: center; gap: 10px; font-size: 13px;">
                                집필: <input type="number" id="rec-pomo-work" value="25" style="width: 50px;" class="rec-input-edit"> 분 / 
                                휴식: <input type="number" id="rec-pomo-rest" value="5" style="width: 50px;" class="rec-input-edit"> 분
                            </div>
                            <div style="display: flex; align-items: center; gap: 15px; flex-wrap: wrap; justify-content: flex-end;">
                                <span id="rec-pomo-display" style="font-size: 18px; font-weight: bold; color: var(--rec-primary);">25:00 (대기 중)</span>
                                <div style="display: flex; gap: 5px;">
                                    <button class="rec-btn" onclick="startPomodoro()">뽀모 시작</button>
                                    <button class="rec-btn" onclick="pausePomodoro()" style="background: #dda7a7; color:#fff;">멈춤</button>
                                    <button class="rec-btn" onclick="resetPomodoro()" style="background: #ccc;">리셋</button>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="rec-section">
                        <h3 id="rec-cal-month-title">📅 월간 집필 통계
                            <button class="rec-btn" onclick="toggleRecCalendar()" id="rec-cal-toggle-btn" style="font-size: 11px; padding: 4px 8px;">접기 🔼</button>
                        </h3>
                        <div class="rec-cal-wrap" id="rec-calendar-wrap" style="display: block;">
                            <div class="rec-cal-grid" id="rec-calendar-grid"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="video-background" id="bgContainer"><div id="bgMediaWrapper"></div></div>
        <div class="overlay"></div>
        
        <button id="restorePanelBtn" onclick="toggleSidePanel()" style="display:none; position:fixed; right:20px; bottom:20px; z-index:9999; background:#6c5ce7; color:white; border:none; border-radius:50px; padding:12px 18px; font-size:14px; font-weight:bold; cursor:pointer; box-shadow:0 4px 10px rgba(0,0,0,0.5); transition: transform 0.2s;">💬 패널 열기</button>

        <div class="main-container">
            <div class="card-grid" id="cardGrid"></div>
            <div class="side-panel">
                <div class="panel-box" style="flex-shrink: 0;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 4px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <h3 style="margin: 0; font-size: 15px; line-height: 20px;">👑 대시보드</h3>
                            <button onclick="toggleSidePanel()" style="background:#636e72; color:white; border:none; border-radius:3px; padding:3px 6px; font-size:10px; cursor:pointer; font-weight:bold;" title="집중 모드: 패널 숨기기">➡️ 접기</button>
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%; margin-top: 5px;">
                            <div style="display: flex; gap: 4px; width: 100%;">
                                <button class="settings-toggle-btn" style="background:#27ae60; color:white; flex: 1; padding: 6px 0;" onclick="toggleEmptySlots()">👀 빈자리</button>
                                <button class="settings-toggle-btn" style="background:#0984e3; color:white; flex: 1; padding: 6px 0;" onclick="addMySlot()">➕ 자리</button>
                            </div>
                            <div style="display: flex; gap: 4px; width: 100%;">
                                <button class="settings-toggle-btn" style="background:#636e72; color:white; flex: 1; padding: 6px 0;" onclick="openModal('settingsModal')">⚙️ 내 배경</button>
                                <button class="settings-toggle-btn" style="background:#e1b12c; color:white; flex: 1; padding: 6px 0;" onclick="openModal('attendanceModal')">🏆 출석현황</button>
                            </div>
                            <button class="settings-toggle-btn" style="background:#ff7675; color:white; width: 100%; text-align: center; padding: 6px 0;" onclick="openModal('noticeModal')">📢 공지</button>
                            <button id="adminLogBtn" class="settings-toggle-btn" style="background:#8e44ad; color:white; width: 100%; text-align: center; padding: 6px 0; margin-top: 4px; display: none;" onclick="openModal('adminLogModal')">👑 출입 기록</button>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 8px;">
                        <div>
                            <span id="connStatus" class="status-indicator status-offline" style="margin-left: 0;">연결 중...</span>
                            <span style="font-size:12px; margin-left: 5px;">인원: <b id="userCount" style="color:#ff7675;">0명</b></span>
                        </div>
                    </div>
                    <p style="margin-top:4px; font-size:11px; color:#aaa; line-height:1.5;">명단:<br><span id="userListStr" style="display:flex; flex-wrap:wrap; gap:4px; margin-top:2px;"></span></p>
                </div>
                <div class="panel-box chat-box">
                    <div style="display:flex; justify-content:space-between; align-items:center; gap: 4px; flex-wrap: wrap;">
                        <h3 style="font-size: 14px; white-space: nowrap;">💬 실시간 채팅</h3>
                        <div style="display:flex; gap: 2px;">
                            <button onclick="forceRecoverWebRTC()" class="recovery-btn" style="font-size:9px; padding:2px 4px;">🔄 복구</button>
                            <button onclick="clearChat()" style="font-size:9px; padding:2px 4px; background:#636e72; border:none; color:white; border-radius:3px; cursor:pointer;">청소</button>
                        </div>
                    </div>
                    <div id="chatHistory"></div>
                    <div class="chat-input">
                        <input type="text" id="chatInput" placeholder="메시지 입력..." onkeypress="if(event.key==='Enter') sendChat()">
                        <button onclick="sendChat()">전송</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const ROOM_PASSWORD = "7777"; 
            const ADMIN_PASSWORD = "4717"; 
            const ADMIN_NICKNAME = "부엉";
            window.rawNotice = ""; 
            window.isHideEmpty = false; 
            window.attendanceData = {}; 
            window.adminLogData = []; 
            
            window.trackersData = {}; 
            window.currentViewingUser = "";
            window.goalAchieved = false;
            let recTotalSeconds = 0;
            let recMainTimerInterval = null;
            let recPomoInterval = null;
            let recPomoSeconds = 25 * 60;
            let recIsWorking = true;
            window.recViewYear = new Date().getFullYear();
            window.recViewMonth = new Date().getMonth() + 1;

            function setMediaBitrate(sdp, bitrate) {
                let lines = sdp.split('\n');
                let line = -1;
                for (let i = 0; i < lines.length; i++) {
                    if (lines[i].indexOf('m=video') === 0) { line = i; break; }
                }
                if (line === -1) return sdp;
                lines.splice(line + 1, 0, 'b=AS:' + bitrate);
                return lines.join('\n');
            }

            function toggleSidePanel() {
                const container = document.querySelector('.main-container');
                const panel = document.querySelector('.side-panel');
                const restoreBtn = document.getElementById('restorePanelBtn');
                
                if (panel.style.display === 'none') {
                    panel.style.display = 'flex';
                    container.style.gridTemplateColumns = 'minmax(0, 1fr) 240px';
                    restoreBtn.style.display = 'none';
                } else {
                    panel.style.display = 'none';
                    container.style.gridTemplateColumns = '1fr'; 
                    restoreBtn.style.display = 'block';
                }
            }

            async function updateLoginUserCount() {
                try {
                    const res = await fetch('/user_count');
                    const data = await res.json();
                    const countEl = document.getElementById('loginUserCount');
                    if(countEl) countEl.innerText = `🔥 현재 달리고 있는 작가님: ${data.count}명`;
                } catch(e) {
                    const countEl = document.getElementById('loginUserCount');
                    if(countEl) countEl.innerText = `🔥 현재 달리고 있는 작가님: ?명`;
                }
            }
            updateLoginUserCount();
            let countInterval = setInterval(() => {
                if(document.getElementById('loginOverlay').style.display !== 'none') {
                    updateLoginUserCount();
                } else {
                    clearInterval(countInterval); 
                }
            }, 10000);

            function openModal(modalId) { 
                document.getElementById(modalId).style.display = 'flex'; 
                if (modalId === 'attendanceModal') { renderAttendanceBoard(); } 
                if (modalId === 'adminLogModal') { renderAdminLog(); } 
            }
            function closeModal(modalId) { 
                document.getElementById(modalId).style.display = 'none'; 
                if (modalId === 'recordModal' && window.myNickname && window.trackersData[window.myNickname]) {
                    changeThemeColor(window.trackersData[window.myNickname].themeColor || "#d87093");
                }
            }

            function formatLogTime(ts) { const now = new Date(ts * 1000); const m = now.getMonth() + 1; const d = now.getDate(); return `${m}/${d} ${now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`; }
            function renderAdminLog() { const container = document.getElementById('adminLogContent'); if (!window.adminLogData || window.adminLogData.length === 0) { container.innerHTML = "기록이 없습니다."; return; } container.innerHTML = window.adminLogData.map(log => `<div style="margin-bottom: 4px;">[${formatLogTime(log.time)}] <b style="color:#ffeaa7;">${log.msg}</b></div>`).join(""); container.scrollTop = container.scrollHeight; }
            
            function renderAttendanceBoard() {
                const now = new Date(); const y = now.getFullYear(); const m = now.getMonth() + 1; const today = now.getDate(); const monthStr = `${y}-${String(m).padStart(2, '0')}`;
                const calTitleEl = document.getElementById('calMonthTitle'); if (calTitleEl) calTitleEl.innerText = `🍀 ${y}년 ${m}월 내 출석부`;
                const grid = document.getElementById('calendarGrid');
                if (grid) {
                    const firstDay = new Date(y, m - 1, 1).getDay(); const daysInMonth = new Date(y, m, 0).getDate(); let html = '';
                    const daysOfWeek = ['일','월','화','수','목','금','토']; daysOfWeek.forEach(d => { html += `<div style="text-align:center; font-size:10px; color:#ffeaa7; margin-bottom: 4px;">${d}</div>`; });
                    for(let i=0; i<firstDay; i++) { html += `<div></div>`; }
                    const myName = window.myNickname || "익명"; const myAtt = (window.attendanceData[monthStr] && window.attendanceData[monthStr][myName]) || [];
                    for(let i=1; i<=daysInMonth; i++) {
                        const isToday = (i === today); const isStamped = myAtt.includes(i); let cls = "cal-day";
                        if (isToday && !isStamped) cls += " today"; if (isStamped) cls += " stamped"; const content = isStamped ? '🍀' : i;
                        if (isToday && !isStamped) { html += `<div class="${cls}" onclick="stampAttendance(${y}, ${m}, ${i})" title="오늘 출석 도장 찍기!">${content}</div>`; } else { html += `<div class="${cls}">${content}</div>`; }
                    }
                    grid.innerHTML = html;
                }
                const titleEl = document.getElementById('rankTitle'); if (titleEl) titleEl.innerText = `🏆 ${m}월 모두의 랭킹`;
                const monthData = window.attendanceData[monthStr] || {}; let rankArr = []; let todayAttendees = [];
                for (let user in monthData) { const stamps = monthData[user]; rankArr.push({ name: user, count: stamps.length }); if (stamps.includes(today)) { todayAttendees.push(user); } }
                rankArr.sort((a, b) => b.count - a.count); let rankHtml = '';
                if (rankArr.length === 0) { rankHtml = '아직 이번 달 출석한 사람이 없어!'; } else { rankArr.forEach((item, idx) => { let medal = '🏅'; if (idx === 0) medal = '🥇'; else if (idx === 1) medal = '🥈'; else if (idx === 2) medal = '🥉'; rankHtml += `<div style="${(idx < 3) ? 'font-weight:bold; color:#fff;' : ''} margin-bottom: 4px;">${medal} ${item.name} : ${item.count}일</div>`; }); }
                let todayHtml = todayAttendees.length === 0 ? '아직 오늘 출석한 사람이 없어! 빨리 1빠 찍어!' : todayAttendees.map(u => `<span style="background:rgba(39, 174, 96, 0.6); padding:4px 8px; border-radius:4px; font-weight:bold;">🍀 ${u}</span>`).join('');
                const rankEl = document.getElementById('attRankingList'); const todayEl = document.getElementById('attTodayList');
                if (rankEl) rankEl.innerHTML = rankHtml; if (todayEl) todayEl.innerHTML = todayHtml;
            }
            function stampAttendance(y, m, d) { const monthStr = `${y}-${String(m).padStart(2, '0')}`; const myName = window.myNickname || "익명"; if (window.attendanceData && window.attendanceData[monthStr] && window.attendanceData[monthStr][myName] && window.attendanceData[monthStr][myName].includes(d)) return; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "attendance", month: monthStr, day: d, nickname: myName })); } }
            function autoStampToday() { const now = new Date(); const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`; const myName = window.myNickname; if (!myName) return; const myAtt = (window.attendanceData[monthStr] && window.attendanceData[monthStr][myName]) || []; if (!myAtt.includes(now.getDate())) { stampAttendance(now.getFullYear(), now.getMonth() + 1, now.getDate()); } }
            function loadLocalBackground() { const bgType = localStorage.getItem('myBgType'); const bgData = localStorage.getItem('myBgData'); if (bgType === 'image' && bgData) { document.getElementById('bgMediaWrapper').innerHTML = `<img src="${bgData}" alt="Full Background">`; } else if (bgType === 'youtube' && bgData) { document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${bgData}?autoplay=1&mute=1&loop=1&playlist=${bgData}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`; } }
            function makeLinksClickable(text) { return text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" style="color: #ffeaa7; text-decoration: underline; padding: 0 4px;" onclick="event.stopPropagation()">$1</a>'); }
            function formatNotice(text) { return !text ? "" : makeLinksClickable(text).replace(/\n/g, '<br>'); }
            function addNotice() { const newVal = prompt("새로 추가할 공지를 적어주세요!\n(새 공지는 맨 위로 올라갑니다)"); if (newVal !== null && newVal.trim() !== "") { const combined = window.rawNotice ? ("📌 " + newVal + "\n\n" + window.rawNotice) : ("📌 " + newVal); if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "update_notice", notice: combined })); window.rawNotice = combined; document.getElementById('noticeText').innerHTML = formatNotice(combined); } } }
            function editNotice() { const newVal = prompt("기존 공지를 전부 지우고 새로 쓰거나, 직접 글을 수정하세요!", window.rawNotice); if (newVal !== null) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "update_notice", notice: newVal })); window.rawNotice = newVal; document.getElementById('noticeText').innerHTML = formatNotice(newVal); } } }
            function toggleEmptySlots() { window.isHideEmpty = !window.isHideEmpty; applyEmptySlotVisibility(); }
            function applyEmptySlotVisibility() { cardData.forEach((card, index) => { const cardEl = document.getElementById(`card-card-${index}`); if (cardEl) { if (window.isHideEmpty && card.user.startsWith("자리")) { cardEl.style.display = "none"; } else { cardEl.style.display = "flex"; } } }); }
            function addMySlot() { const myName = window.myNickname || "익명"; let emptyIdx = -1; for (let i = 0; i < cardData.length; i++) { if (cardData[i].user.startsWith("자리")) { emptyIdx = i; break; } } if (emptyIdx !== -1) { const inputEl = document.getElementById(`username-${emptyIdx}`); if (inputEl) inputEl.value = myName; updateUsername(emptyIdx, myName); } else { alert("아앗! 방에 빈자리가 하나도 안 남았어 누나!"); } }
            function checkLogin() { document.getElementById('loginOverlay').style.display = 'flex'; const savedNick = localStorage.getItem('mySavedNickname'); if (savedNick) { document.getElementById('nickInput').value = savedNick; document.getElementById('pwInput').focus(); } }
            
            function login() { 
                const inputPw = document.getElementById('pwInput').value; 
                const inputNick = document.getElementById('nickInput').value.trim(); 
                if (!inputNick) { alert("누군지 알 수 있게 닉네임을 적어줘 누나!"); return; } 
                if (inputNick === ADMIN_NICKNAME) { 
                    if (inputPw !== ADMIN_PASSWORD) { alert("앗! 이 닉네임은 방장(누나) 전용이야! 비밀번호가 틀렸어!"); return; } 
                    window.isAdmin = true; document.getElementById('adminLogBtn').style.display = 'block'; 
                } else { 
                    if (inputPw !== ROOM_PASSWORD) { alert("비밀번호가 틀렸어! 다시 확인해봐."); return; } 
                    window.isAdmin = false; 
                } 
                window.myNickname = inputNick; 
                localStorage.setItem('mySavedNickname', inputNick); 
                document.getElementById('loginOverlay').style.display = 'none'; 
                initCards(); 
                connectWebSocket(); 
                loadLocalBackground(); 
                loadMyLocalTrackerData(); 
            }
            
            function kickUser(nickname) { if(confirm(`${nickname} 님을 방에서 강제로 쫓아낼까?`)) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "kick", target_nick: nickname })); } } }

            let ws = null; let pingInterval = null; 
            const cardData = Array.from({length: 16}, (_, i) => ({ id: i+1, user: `자리{i+1}`, card_bg: null, is_mosaic: false, is_large: false, status: 0, is_local_hidden: false }));
            const myStreams = {}; const peerConnections = {}; const candidateBuffers = {}; const expectedShares = {}; const myOwnedSlots = new Set(); 
            const rtcConfig = { iceServers: [ { urls: 'stun:stun.l.google.com:19302' }, { urls: 'stun:stun1.l.google.com:19302' } ] };

            function getEmptySlotHTML(username) { if (!username || username.startsWith("자리")) { return `<div style="position:relative; z-index:2; width:100%; text-align:center;"><span style="font-size:11px; color:#aaa;">화면 미공유 중</span></div>`; } else { return `<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; z-index:2; text-align:center; padding:10px; width:100%; height:100%;"><span style="font-size:22px; font-weight:900; color:#fff; text-shadow: 2px 2px 5px rgba(0,0,0,0.9); margin-bottom:4px;">${username}</span><span style="font-size:11px; color:#aaa;">화면 미공유 중</span></div>`; } }
            
            function renderBox(index) { 
                const box = document.getElementById(`stream-box-${index}`); if (!box) return; 
                const existingVideo = box.querySelector('video'); if (existingVideo) existingVideo.remove(); 
                const card = cardData[index]; 
                if (card.status > 0) { 
                    let textMsg = ""; if (card.status === 1) textMsg = "🍽️ 식사중"; else if (card.status === 2) textMsg = "☕ 휴식중"; else if (card.status === 3) textMsg = "💤 수면중"; 
                    box.innerHTML = `<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; height:100%; background: rgba(0,0,0,0.7); z-index: 5; position: absolute; top:0; left:0;"><div style="font-size: 28px; font-weight: 900; color: #fff; text-shadow: 2px 2px 6px rgba(0,0,0,0.8);">${textMsg}</div></div>`; 
                } else { 
                    box.innerHTML = getEmptySlotHTML(card.user); 
                } 
            }
            
            function updateStatusUI(index, status) { const btn = document.getElementById(`share-btn-status-${index}`); if (btn) { if (status > 0) { btn.innerText = "끄기"; btn.style.background = "#d63031"; } else { btn.innerText = "상태"; btn.style.background = "#8e44ad"; } } }
            function handleStatusMainClick(index) { const isMine = ((cardData[index].user === window.myNickname) && window.myNickname) || window.isAdmin; if (!isMine) { alert("자기 자리 상태만 바꿀 수 있어 누나!"); return; } if (cardData[index].status > 0) { setStatus(index, 0); } else { toggleStatusMenu(index); } }
            function toggleStatusMenu(index) { for(let i=0; i<16; i++) { if (i !== index) { const m = document.getElementById(`status-menu-${i}`); if (m) m.style.display = 'none'; } } const menu = document.getElementById(`status-menu-${index}`); if (menu) { menu.style.display = (menu.style.display === 'flex') ? 'none' : 'flex'; } }
            function setStatus(index, s) { const menu = document.getElementById(`status-menu-${index}`); if(menu) menu.style.display = 'none'; cardData[index].status = s; updateStatusUI(index, s); if (s !== 0 && myStreams[index]) { stopShare(index); } if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "status_update", index: index, status: s })); } renderBox(index); }
            document.addEventListener('click', function(event) { if (!event.target.closest('.status-wrap')) { for(let i=0; i<16; i++) { const m = document.getElementById(`status-menu-${i}`); if(m) m.style.display = 'none'; } } });
            
            function logChat(sender, msg, timeStr) { const history = document.getElementById('chatHistory'); const tSpan = timeStr ? `<span style="font-size:10px; color:#636e72; margin-left:6px;">${timeStr}</span>` : ''; history.innerHTML += `<div style="margin-bottom: 5px;"><b>${sender}</b>: ${msg}${tSpan}</div>`; history.scrollTop = history.scrollHeight; }
            function clearChat() { if (confirm("채팅창을 전부 깨끗하게 지울까?")) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "clear_chat" })); } } }
            
            function forceRecoverWebRTC() { 
                if (ws && ws.readyState === WebSocket.OPEN) { 
                    const ownedArr = Array.from(myOwnedSlots);
                    ws.send(JSON.stringify({ type: "set_nickname", nickname: window.myNickname, owned: ownedArr }));
                    ws.send(JSON.stringify({ type: "request_existing_shares" })); 
                    for (let idx in expectedShares) { 
                        const sharerId = expectedShares[idx]; 
                        if (sharerId && sharerId !== ws.clientId) { 
                            ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: sharerId })); 
                        } 
                    } 
                } 
                alert("화면 공유 통신선을 강제로 다시 뚫고 있습니다! 2~3초만 기다려주세요!"); 
            }

            function toggleLocalHide(index) {
                cardData[index].is_local_hidden = !cardData[index].is_local_hidden;
                const btn = document.getElementById(`hide-btn-${index}`);
                const streamBox = document.getElementById(`stream-box-${index}`);
                if (cardData[index].is_local_hidden) {
                    if(btn) { btn.innerText = "보기"; btn.style.background = "#e84393"; }
                    if(streamBox) { streamBox.style.visibility = "hidden"; }
                } else {
                    if(btn) { btn.innerText = "가리기"; btn.style.background = "#2d3436"; }
                    if(streamBox) { streamBox.style.visibility = "visible"; }
                }
            }

            function initCards() {
                const grid = document.getElementById('cardGrid'); grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    let bgStyle = card.card_bg ? `background-image: url('${card.card_bg}');` : ''; let mosaicBtnBg = card.is_mosaic ? '#e17055' : '#636e72'; let mosaicBtnText = card.is_mosaic ? '해제' : '모자이크'; let sizeBtnText = card.is_large ? '작게' : '크게'; let sizeBtnBg = card.is_large ? '#e67e22' : '#f39c12'; let largeClass = card.is_large ? ' card-large' : ''; let statusBtnText = card.status > 0 ? '끄기' : '상태'; let statusBtnBg = card.status > 0 ? '#d63031' : '#8e44ad';
                    let hideBtnText = card.is_local_hidden ? '보기' : '가리기'; let hideBtnBg = card.is_local_hidden ? '#e84393' : '#2d3436'; let boxVisibility = card.is_local_hidden ? 'hidden' : 'visible';
                    let myOrder = (card.user === window.myNickname && window.myNickname) ? -1 : 0;
                    grid.innerHTML += `
                        <div class="timer-card${largeClass}" id="card-card-${index}" style="${bgStyle} order: ${myOrder};">
                            <div class="card-header">
                                <div style="display: flex; gap: 4px; align-items: center; width: 100%;">
                                    <input type="text" id="username-${index}" value="${card.user}" style="flex-grow: 1; min-width: 0; padding: 4px; font-size: 11px; font-weight: bold; text-align: center; background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.4); color: white; border-radius: 3px;" oninput="updateUsername(${index}, this.value)">
                                    <button onclick="openRecordModalByIndex(${index})" class="share-btn" style="background:#6c5ce7; padding:4px 6px; flex-grow:0;">✍️ 집필기록</button>
                                </div>
                                <div class="btn-group" style="margin-top: 4px;">
                                    <button class="share-btn" id="share-btn-screen-${index}" style="background:#ff7675;" onclick="toggleShare(${index}, 'screen')">화공</button>
                                    <button class="share-btn" id="share-btn-cam-${index}" style="background:#0984e3;" onclick="toggleShare(${index}, 'cam')">캠</button>
                                    <div style="position:relative; display:flex; flex-grow:1;" class="status-wrap">
                                        <button class="share-btn" id="share-btn-status-${index}" style="background:${statusBtnBg}; width:100%;" onclick="handleStatusMainClick(${index})">${statusBtnText}</button>
                                        <div id="status-menu-${index}" style="display:none; position:absolute; top:100%; left:50%; transform:translateX(-50%); background:rgba(30,30,40,0.95); border:1px solid #8e44ad; border-radius:4px; flex-direction:column; z-index:100; min-width:80px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); padding: 4px; margin-top: 2px;">
                                            <button onclick="setStatus(${index}, 1)" style="background:transparent; border:none; color:white; padding:6px 4px; text-align:center; cursor:pointer; font-size:12px; width:100%; border-radius:3px; white-space:nowrap;" onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'">🍽️ 식사</button>
                                            <button onclick="setStatus(${index}, 2)" style="background:transparent; border:none; color:white; padding:6px 4px; text-align:center; cursor:pointer; font-size:12px; width:100%; border-radius:3px; white-space:nowrap;" onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'">☕ 휴식</button>
                                            <button onclick="setStatus(${index}, 3)" style="background:transparent; border:none; color:white; padding:6px 4px; text-align:center; cursor:pointer; font-size:12px; width:100%; border-radius:3px; white-space:nowrap;" onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'">💤 수면</button>
                                        </div>
                                    </div>
                                    <button class="share-btn" id="share-btn-mosaic-${index}" style="background:${mosaicBtnBg};" onclick="handleMosaicClick(${index})">${mosaicBtnText}</button>
                                    <button class="share-btn" id="size-btn-${index}" style="background:${sizeBtnBg};" onclick="toggleSize(${index})">${sizeBtnText}</button>
                                    <button class="share-btn" id="sound-toggle-btn-${index}" style="background:#00b894; display:none;" onclick="toggleViewerSound(${index})">음소거</button>
                                    <button class="share-btn" id="hide-btn-${index}" style="background:${hideBtnBg};" onclick="toggleLocalHide(${index})">${hideBtnText}</button>
                                    <button class="share-btn" style="background:#b2bec3; color:#2d3436; flex-grow:1;" onclick="document.getElementById('card-file-${index}').click()" title="카드 배경화면 변경">🖼️ 배경</button>
                                    <input type="file" id="card-file-${index}" style="display:none;" accept="image/jpeg, image/png, image/webp" onchange="setCardBackground(${index}, event)">
                                </div>
                            </div>
                            <div style="position: relative; flex-grow: 1; display: flex; flex-direction: column; border-radius: 8px; overflow: hidden; margin-top: 6px;">
                                <div class="card-stream-box" id="stream-box-${index}" style="margin-top:0; border-radius:0; visibility:${boxVisibility};"></div>
                            </div>
                        </div>
                    `;
                });
                cardData.forEach((_, i) => renderBox(i)); applyEmptySlotVisibility();
            }

            function toggleSize(index) { const newState = !cardData[index].is_large; cardData[index].is_large = newState; applySizeUI(index, newState); }
            function applySizeUI(index, isLarge) { const cardEl = document.getElementById(`card-card-${index}`); const btn = document.getElementById(`size-btn-${index}`); if (cardEl) { if (isLarge) cardEl.classList.add('card-large'); else cardEl.classList.remove('card-large'); } if (btn) { btn.innerText = isLarge ? "작게" : "크게"; btn.style.background = isLarge ? "#e67e22" : "#f39c12"; } }
            function handleMosaicClick(index) { const isMyStream = !!myStreams[index]; if (!isMyStream && !window.isAdmin) { alert("본인이 화면 공유 중이 아니면 만질 수 없어!"); return; } const newState = !cardData[index].is_mosaic; cardData[index].is_mosaic = newState; applyMosaicUI(index, newState); if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "toggle_mosaic", index: index, is_mosaic: newState })); } }
            function toggleViewerSound(index) { const vid = document.getElementById(`remote-video-${index}`); const btn = document.getElementById(`sound-toggle-btn-${index}`); if (!vid) return; vid.muted = !vid.muted; if (vid.muted) { btn.innerText = "소리켜기"; btn.style.background = "#b2bec3"; } else { btn.innerText = "음소거"; btn.style.background = "#00b894"; } }
            function applyMosaicUI(index, isMosaic) { const btn = document.getElementById(`share-btn-mosaic-${index}`); if (btn) { btn.innerText = isMosaic ? "해제" : "모자이크"; btn.style.background = isMosaic ? "#e17055" : "#636e72"; } const remoteVideo = document.getElementById(`remote-video-${index}`); const localVideo = document.getElementById(`video-${index}`); const activeFilter = isMosaic ? 'blur(5px)' : 'none'; if (remoteVideo) { remoteVideo.style.filter = activeFilter; } if (localVideo) { localVideo.style.filter = activeFilter; } }
            function updateUsername(index, val) { 
                cardData[index].user = val; 
                const myName = window.myNickname || "익명"; 
                if (val === myName) { myOwnedSlots.add(index); } else { myOwnedSlots.delete(index); } 
                const cardEl = document.getElementById(`card-card-${index}`);
                if (cardEl) { cardEl.style.order = (val === myName && myName) ? -1 : 0; }
                const box = document.getElementById(`stream-box-${index}`); 
                if (box && !box.querySelector('video')) { renderBox(index); } 
                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "username_change", index: index, user: val })); } 
                applyEmptySlotVisibility(); 
            }
            function setCardBackground(index, event) { const file = event.target.files[0]; if (!file) return; if (file.type === "image/gif") { alert("움짤(GIF)은 올릴 수 없어 누나!"); event.target.value = ""; return; } const reader = new FileReader(); reader.onload = function(e) { const dataUrl = e.target.result; cardData[index].card_bg = dataUrl; const cardEl = document.getElementById(`card-card-${index}`); if (cardEl) { cardEl.style.backgroundImage = `url('${dataUrl}')`; } if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "card_bg_change", index: index, dataUrl: dataUrl })); } }; reader.readAsDataURL(file); }
            function setLocalBackground(event) { const file = event.target.files[0]; if (!file) return; if (file.type === "image/gif") { alert("움짤(GIF)은 올릴 수 없어 누나!"); event.target.value = ""; return; } const reader = new FileReader(); reader.onload = function(e) { const dataUrl = e.target.result; document.getElementById('bgMediaWrapper').innerHTML = `<img src="${dataUrl}" alt="Full Background">`; localStorage.setItem('myBgType', 'image'); try { localStorage.setItem('myBgData', dataUrl); } catch (err) { alert("사진 용량이 커서 다음 접속 시 풀릴 수 있어!"); } }; reader.readAsDataURL(file); }
            function setYoutubeBackground() { const inputVal = document.getElementById('bgYoutubeInput').value; const videoId = extractYoutubeId(inputVal); if (!videoId) { alert("유튜브 링크가 올바르지 않습니다."); return; } document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&loop=1&playlist=${videoId}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`; localStorage.setItem('myBgType', 'youtube'); localStorage.setItem('myBgData', videoId); }
            
            async function toggleShare(index, type) {
                const box = document.getElementById(`stream-box-${index}`); const btnScreen = document.getElementById(`share-btn-screen-${index}`); const btnCam = document.getElementById(`share-btn-cam-${index}`);
                if (myStreams[index]) { stopShare(index); return; }
                if (cardData[index].status > 0) { cardData[index].status = 0; updateStatusUI(index, 0); if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "status_update", index: index, status: 0 })); } }
                try {
                    let stream;
                    if (type === 'screen') { 
                        stream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: "always", frameRate: 10 }, audio: true }); 
                        btnScreen.innerText = "중지"; btnScreen.style.background = "#d63031"; btnCam.style.display = "none"; 
                    }
                    else { 
                        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 20, max: 24 } }, audio: false }); 
                        btnCam.innerText = "중지"; btnCam.style.background = "#d63031"; btnScreen.style.display = "none"; 
                    }
                    myStreams[index] = stream; const myName = window.myNickname || "익명"; const userEl = document.getElementById(`username-${index}`); if (userEl) userEl.value = myName; updateUsername(index, myName);
                    let filterStyle = cardData[index].is_mosaic ? `filter: blur(5px);` : ''; box.innerHTML = `<video id="video-${index}" autoplay playsinline muted disablePictureInPicture style="${filterStyle}"></video>`;
                    const localVideo = document.getElementById(`video-${index}`); localVideo.srcObject = stream; localVideo.play().catch(e => console.log(e));
                    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "start_share", index: index })); }
                    stream.getVideoTracks()[0].onended = () => { stopShare(index); };
                } catch (err) { console.error("미디어 캡처 에러:", err); }
            }

            function stopShare(index) {
                if (myStreams[index]) { myStreams[index].getTracks().forEach(track => track.stop()); delete myStreams[index]; }
                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "stop_share", index: index })); }
                renderBox(index); const btnScreen = document.getElementById(`share-btn-screen-${index}`); const btnCam = document.getElementById(`share-btn-cam-${index}`);
                if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; }
                if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; }
                const soundBtn = document.getElementById(`sound-toggle-btn-${index}`); if (soundBtn) { soundBtn.style.display = "none"; }
            }

            setInterval(() => {
                if (!ws || ws.readyState !== WebSocket.OPEN || !ws.clientId) return;
                for (let idx in expectedShares) {
                    const sharerId = expectedShares[idx]; if (sharerId === ws.clientId) continue; 
                    const pcKey = `${idx}_${sharerId}`; const pc = peerConnections[pcKey]; let isConnectionDead = false;
                    if (!pc) { isConnectionDead = true; } else { const state = pc.iceConnectionState; if (state === 'disconnected' || state === 'failed' || state === 'closed') { isConnectionDead = true; } }
                    if (isConnectionDead) { if (pc) { try { pc.close(); } catch(e) {} } delete peerConnections[pcKey]; ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: sharerId })); }
                }
            }, 5000);

            function extractYoutubeId(url) { if (!url) return null; url = url.trim(); if (url.length === 11) return url; const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/; const match = url.match(regExp); return (match && match[2].length === 11) ? match[2] : null; }

            function connectWebSocket() {
                const loc = window.location; let wsProtocol = loc.protocol === "https:" ? "wss://" : "ws://"; const wsUrl = wsProtocol + loc.host + "/ws";
                try {
                    ws = new WebSocket(wsUrl);
                    ws.onopen = function() {
                        const statusEl = document.getElementById('connStatus'); statusEl.innerText = "연결됨"; statusEl.className = "status-indicator status-online";
                        const myNick = window.myNickname || "익명"; const ownedArr = Array.from(myOwnedSlots);
                        ws.send(JSON.stringify({ type: "set_nickname", nickname: myNick, owned: ownedArr })); autoStampToday();
                        
                        for (let idx in myStreams) {
                            if (myStreams[idx]) {
                                ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx) }));
                            }
                        }
                        ws.send(JSON.stringify({ type: "request_existing_shares" }));
                        for (let idx in expectedShares) {
                            const sharerId = expectedShares[idx];
                            if (sharerId && sharerId !== ws.clientId) {
                                ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: sharerId }));
                            }
                        }

                        if (pingInterval) clearInterval(pingInterval); pingInterval = setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "ping" })); } }, 5000); 
                    };
                    ws.onmessage = async function(event) {
                        try {
                            const data = JSON.parse(event.data);
                            if (data.type === "pong") { return; }
                            else if (data.type === "kicked") { alert("방장에 의해 방에서 쫓겨났어!"); localStorage.removeItem('mySavedNickname'); window.location.reload(); }
                            else if (data.type === "duplicate_kicked") {
                                alert("다른 기기(또는 창)에서 동일한 닉네임이 접속되어 이전 창은 얌전하게 종료할게 누나!");
                                if (pingInterval) clearInterval(pingInterval);
                                ws.onclose = null;
                                ws.close();
                            }
                            else if (data.type === "chat_cleared") { document.getElementById('chatHistory').innerHTML = ""; }
                            else if (data.type === "user_list") {
                                document.getElementById('userCount').innerText = data.count + "명";
                                let listHtml = data.users.map(u => { let kickBtn = ''; if (window.isAdmin && u.nickname !== window.myNickname) { kickBtn = `<button onclick="kickUser('${u.nickname}')" style="background:#d63031; border:none; color:white; border-radius:3px; padding:1px 4px; font-size:9px; cursor:pointer; margin-left:4px;">강퇴</button>`; } return `<span style="background:rgba(255,255,255,0.1); padding:3px 8px; border-radius:4px; display:inline-flex; align-items:center;"><b style="color:white;">${u.nickname}</b>${kickBtn}</span>`; }).join("");
                                document.getElementById('userListStr').innerHTML = listHtml;
                            }
                            else if (data.type === "chat") { logChat(data.senderName, data.msg, data.time); } 
                            else if (data.type === "update_notice") { window.rawNotice = data.notice; document.getElementById('noticeText').innerHTML = formatNotice(data.notice); }
                            else if (data.type === "status_update") { cardData[data.index].status = data.status; updateStatusUI(data.index, data.status); const box = document.getElementById(`stream-box-${data.index}`); if (box && !box.querySelector('video')) { renderBox(data.index); } }
                            else if (data.type === "init_state") {
                                const state = data.state;
                                if (state.global_notice) { window.rawNotice = state.global_notice; document.getElementById('noticeText').innerHTML = formatNotice(state.global_notice); }
                                if (state.attendance) { window.attendanceData = state.attendance; }
                                if (state.admin_log) { window.adminLogData = state.admin_log; if (window.isAdmin && document.getElementById('adminLogModal').style.display === 'flex') { renderAdminLog(); } }
                                if (state.trackers) { window.trackersData = state.trackers; }
                                if (state.cards) {
                                    state.cards.forEach((card, i) => {
                                        if (cardData[i]) {
                                            cardData[i].user = card.user; cardData[i].card_bg = card.card_bg; cardData[i].is_mosaic = card.is_mosaic || false; cardData[i].is_large = card.is_large || false; cardData[i].status = card.status || 0;
                                            cardData[i].is_local_hidden = cardData[i].is_local_hidden || false; 
                                            
                                            applyMosaicUI(i, cardData[i].is_mosaic); applySizeUI(i, cardData[i].is_large); updateStatusUI(i, cardData[i].status);
                                            const userEl = document.getElementById(`username-${i}`); if (userEl) userEl.value = card.user;
                                            const cardEl = document.getElementById(`card-card-${i}`); 
                                            if (cardEl) {
                                                if (card.card_bg) { cardEl.style.backgroundImage = `url('${card.card_bg}')`; }
                                                cardEl.style.order = (card.user === window.myNickname && window.myNickname) ? -1 : 0;
                                            }
                                            const hideBtn = document.getElementById(`hide-btn-${i}`);
                                            if(hideBtn) {
                                                hideBtn.innerText = cardData[i].is_local_hidden ? "보기" : "가리기";
                                                hideBtn.style.background = cardData[i].is_local_hidden ? "#e84393" : "#2d3436";
                                            }
                                            const box = document.getElementById(`stream-box-${i}`);
                                            if(box) box.style.visibility = cardData[i].is_local_hidden ? "hidden" : "visible";
                                            if (box && !box.querySelector('video')) { renderBox(i); }
                                        }
                                    });
                                }
                                if (state.chat_history) { const historyEl = document.getElementById('chatHistory'); historyEl.innerHTML = ""; state.chat_history.forEach(chat => { const tSpan = chat.time ? `<span style="font-size:10px; color:#636e72; margin-left:6px;">${chat.time}</span>` : ''; historyEl.innerHTML += `<div style="margin-bottom: 5px;"><b>${chat.senderName}</b>: ${chat.msg}${tSpan}</div>`; }); historyEl.scrollTop = historyEl.scrollHeight; }
                                applyEmptySlotVisibility();
                            }
                            else if (data.type === "tracker_update") { 
                                if (!window.trackersData) window.trackersData = {};
                                window.trackersData[data.nickname] = data.tracker_data;
                                if (window.currentViewingUser === data.nickname && document.getElementById('recordModal').style.display === 'flex') {
                                    loadRecordDataIntoUI(data.nickname);
                                }
                            }
                            else if (data.type === "attendance_update") { window.attendanceData = data.attendance; if (document.getElementById('attendanceModal').style.display === 'flex') { renderAttendanceBoard(); } }
                            else if (data.type === "admin_log_update") { if (!window.adminLogData) window.adminLogData = []; window.adminLogData.push(data.log); if (window.adminLogData.length > 100) window.adminLogData.shift(); if (window.isAdmin && document.getElementById('adminLogModal').style.display === 'flex') { renderAdminLog(); } }
                            else if (data.type === "username_change") { 
                                cardData[data.index].user = data.user; 
                                const inputEl = document.getElementById(`username-${data.index}`); 
                                if (inputEl) { inputEl.value = data.user; } 
                                const myName = window.myNickname || "익명"; 
                                if (data.user === myName) { myOwnedSlots.add(data.index); } else { myOwnedSlots.delete(data.index); } 
                                const cardEl = document.getElementById(`card-card-${data.index}`);
                                if (cardEl) { cardEl.style.order = (data.user === myName && myName) ? -1 : 0; }
                                const box = document.getElementById(`stream-box-${data.index}`); 
                                if (box && !box.querySelector('video')) { renderBox(data.index); }
                                applyEmptySlotVisibility(); 
                            } 
                            else if (data.type === "card_bg_change") { cardData[data.index].card_bg = data.dataUrl; const cardEl = document.getElementById(`card-card-${data.index}`); if (cardEl) { cardEl.style.backgroundImage = `url('${data.dataUrl}')`; } } 
                            else if (data.type === "toggle_mosaic") { if (cardData[data.index]) { cardData[data.index].is_mosaic = data.is_mosaic; applyMosaicUI(data.index, data.is_mosaic); } }
                            else if (data.type === "start_share") { const targetIndex = data.index; const sharerId = data.sender; if (data.target && data.target !== ws.clientId) return; expectedShares[targetIndex] = sharerId; if (ws.clientId && sharerId !== ws.clientId && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "request_offer", index: targetIndex, target: sharerId })); } }
                            else if (data.type === "request_offer" && data.target === ws.clientId) { const targetIndex = data.index; const viewerId = data.sender; if (myStreams[targetIndex]) { await createOfferForViewer(targetIndex, viewerId); } }
                            else if (data.type === "offer" && data.target === ws.clientId) {
                                const index = data.index; const senderId = data.sender; const pcKey = `${index}_${senderId}`;
                                if (peerConnections[pcKey]) { try { peerConnections[pcKey].close(); } catch(e) {} }
                                const pc = new RTCPeerConnection(rtcConfig); peerConnections[pcKey] = pc;
                                pc.oniceconnectionstatechange = () => { const state = pc.iceConnectionState; if (state === 'disconnected' || state === 'failed' || state === 'closed') { try { pc.close(); } catch(e) {} delete peerConnections[pcKey]; } };
                                pc.ontrack = (e) => { const box = document.getElementById(`stream-box-${index}`); let filterStyle = cardData[index].is_mosaic ? `filter: blur(5px);` : ''; box.innerHTML = `<video id="remote-video-${index}" autoplay playsinline disablePictureInPicture style="${filterStyle}"></video>`; const remoteVideo = document.getElementById(`remote-video-${index}`); remoteVideo.srcObject = e.streams[0]; remoteVideo.play().catch(err => console.log(err)); const soundBtn = document.getElementById(`sound-toggle-btn-${index}`); if (soundBtn) { soundBtn.style.display = "inline-block"; soundBtn.innerText = "소리끄기"; soundBtn.style.background = "#00b894"; } };
                                pc.onicecandidate = (e) => { if (e.candidate && ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "ice", index: index, target: senderId, candidate: e.candidate })); } };
                                await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
                                if (candidateBuffers[pcKey]) { for (const cand of candidateBuffers[pcKey]) { await pc.addIceCandidate(new RTCIceCandidate(cand)).catch(e => console.log(e)); } delete candidateBuffers[pcKey]; }
                                const answer = await pc.createAnswer(); 
                                const sdpWithBitrate = setMediaBitrate(answer.sdp, 50);
                                await pc.setLocalDescription({ type: answer.type, sdp: sdpWithBitrate });
                                
                                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "answer", index: index, target: senderId, sdp: pc.localDescription })); }
                            } 
                            else if (data.type === "answer" && data.target === ws.clientId) { const pcKey = `${data.index}_${data.sender}`; const pc = peerConnections[pcKey]; if (pc) await pc.setRemoteDescription(new RTCSessionDescription(data.sdp)); } 
                            else if (data.type === "ice" && data.target === ws.clientId) { const pcKey = `${data.index}_${data.sender}`; const pc = peerConnections[pcKey]; if (pc && pc.remoteDescription && pc.remoteDescription.type) { await pc.addIceCandidate(new RTCIceCandidate(data.candidate)).catch(e => console.log(e)); } else { if (!candidateBuffers[pcKey]) candidateBuffers[pcKey] = []; candidateBuffers[pcKey].push(data.candidate); } } 
                            else if (data.type === "stop_share") { const index = data.index; delete expectedShares[index]; for (let key in peerConnections) { if (key.startsWith(`${index}_`)) { try { peerConnections[key].getSenders().forEach(sender => peerConnections[key].removeTrack(sender)); peerConnections[key].close(); } catch(e) {} delete peerConnections[key]; } } renderBox(index); const btnScreen = document.getElementById(`share-btn-screen-${index}`); const btnCam = document.getElementById(`share-btn-cam-${index}`); if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; } if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; } const soundBtn = document.getElementById(`sound-toggle-btn-${index}`); if (soundBtn) { soundBtn.style.display = "none"; } }
                            else if (data.type === "welcome") { ws.clientId = data.clientId; setTimeout(() => { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "request_existing_shares" })); for (let idx in myStreams) { ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx) })); } } }, 800); }
                            else if (data.type === "request_existing_shares") { for (let idx in myStreams) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx), target: data.sender })); } } }
                        } catch(e) { console.error("데이터 처리 에러:", e); }
                    };
                    
                    ws.onclose = function() { 
                        if (pingInterval) clearInterval(pingInterval); 
                        const statusEl = document.getElementById('connStatus'); 
                        if (statusEl) { 
                            statusEl.innerText = "서버 재연결 중..."; 
                            statusEl.className = "status-indicator status-offline"; 
                        } 
                        for (let key in peerConnections) { try { peerConnections[key].close(); } catch(e) {} delete peerConnections[key]; }
                        for (let k in expectedShares) delete expectedShares[k];
                        setTimeout(connectWebSocket, 3000); 
                    };
                } catch(e) { setTimeout(connectWebSocket, 2000); }
            }

            async function createOfferForViewer(index, viewerId) {
                const pcKey = `${index}_${viewerId}`; if (peerConnections[pcKey]) { try { peerConnections[pcKey].close(); } catch(e) {} }
                const pc = new RTCPeerConnection(rtcConfig); peerConnections[pcKey] = pc;
                pc.oniceconnectionstatechange = () => { const state = pc.iceConnectionState; if (state === 'disconnected' || state === 'failed' || state === 'closed') { try { pc.getSenders().forEach(sender => pc.removeTrack(sender)); pc.close(); } catch(e) {} delete peerConnections[pcKey]; } };
                const stream = myStreams[index]; if (stream) { stream.getTracks().forEach(track => pc.addTrack(track, stream)); }
                pc.onicecandidate = (e) => { if (e.candidate && ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "ice", index: index, target: viewerId, candidate: e.candidate })); } };
                
                const offer = await pc.createOffer(); 
                const sdpWithBitrate = setMediaBitrate(offer.sdp, 50);
                await pc.setLocalDescription({ type: offer.type, sdp: sdpWithBitrate });
                
                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "offer", index: index, target: viewerId, sdp: pc.localDescription })); }
            }

            function sendChat() { const input = document.getElementById('chatInput'); const msgText = input.value.trim(); if (!msgText) return; const myName = window.myNickname || "익명"; const now = new Date(); const month = now.getMonth() + 1; const date = now.getDate(); const timeString = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }); const timeStr = `${month}/${date} ${timeString}`; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "chat", senderName: myName, msg: msgText, time: timeStr })); input.value = ''; } }
            
            function openRecordModalByIndex(index) {
                const cardUser = cardData[index].user;
                if (!cardUser || cardUser.startsWith("자리")) {
                    alert("아직 자리에 작가님이 앉지 않았어요! 😅");
                    return;
                }
                openRecordModal(cardUser);
            }

            function openRecordModal(nickname) {
                if (!nickname || nickname.startsWith("자리")) {
                    alert("아직 자리에 작가님이 앉지 않았어요! 😅");
                    return;
                }
                window.currentViewingUser = nickname;
                
                if (!window.trackersData[nickname]) {
                    window.trackersData[nickname] = { targetChars: 5000, doneChars: 0, themeColor: "#d87093", calendar: {}, todos: [], lastDate: "" };
                }
                
                loadRecordDataIntoUI(nickname);
                const modal = document.getElementById('recordModal');
                if (modal) modal.style.display = 'flex';
            }

            function loadRecordDataIntoUI(nickname) {
                const isMe = (nickname === window.myNickname);
                const now = new Date();
                const todayStr = `${now.getFullYear()}-${now.getMonth()+1}-${now.getDate()}`;
                
                window.recViewYear = now.getFullYear();
                window.recViewMonth = now.getMonth() + 1;
                
                if (isMe) {
                    if (window.trackersData && window.trackersData[nickname]) {
                        if (window.trackersData[nickname].lastDate && window.trackersData[nickname].lastDate !== todayStr) {
                            window.trackersData[nickname].doneChars = 0;
                            window.trackersData[nickname].todos = [];
                            window.trackersData[nickname].lastDate = todayStr;
                            recTotalSeconds = 0;
                            localStorage.setItem('doneChars', '0');
                            localStorage.setItem('myTodos', '[]');
                            localStorage.setItem('lastDate', todayStr);
                            
                            if (ws && ws.readyState === WebSocket.OPEN) {
                                ws.send(JSON.stringify({ type: "tracker_update", nickname: nickname, tracker_data: window.trackersData[nickname] }));
                            }
                        }
                    }
                }
                
                let data = window.trackersData[nickname] || { targetChars: 5000, doneChars: 0, themeColor: "#d87093", calendar: {}, todos: [], lastDate: todayStr };

                if (!isMe && data.lastDate && data.lastDate !== todayStr) {
                    data = { ...data, doneChars: 0, todos: [] };
                }

                document.getElementById('rec-title').innerText = `✨ ${nickname} 작가님의 집필 기록방 ✨`;

                document.getElementById('rec-target-chars').value = data.targetChars || 5000;
                document.getElementById('rec-done-chars').value = data.doneChars || 0;
                
                const savedColor = data.themeColor || "#d87093";
                document.getElementById('themeColorPicker').value = savedColor;
                changeThemeColor(savedColor);

                const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
                const dayStr = now.getDate().toString();

                if (isMe && recTotalSeconds === 0) {
                    if (data.lastDate === todayStr && data.calendar && data.calendar[monthStr] && data.calendar[monthStr][dayStr] && data.calendar[monthStr][dayStr].seconds) {
                        recTotalSeconds = data.calendar[monthStr][dayStr].seconds;
                        updateMainTimerDisplay();
                    }
                }

                document.getElementById('rec-target-chars').readOnly = !isMe;
                document.getElementById('rec-done-chars').readOnly = !isMe;
                document.getElementById('themeColorPicker').disabled = !isMe;
                
                document.getElementById('rec-save-btn').style.display = isMe ? "inline-block" : "none";
                document.getElementById('rec-my-timer-area').style.display = isMe ? "block" : "none";
                
                const todoInputArea = document.getElementById('rec-todo-input-area');
                if (todoInputArea) todoInputArea.style.display = isMe ? "flex" : "none";
                
                document.getElementById('rec-pomo-section').style.display = isMe ? "block" : "none";

                const ul = document.getElementById('rec-todo-list');
                ul.innerHTML = '';
                const todos = data.todos || [];
                todos.forEach(todo => {
                    const li = document.createElement('li');
                    const checked = todo.done ? "checked" : "";
                    const compClass = todo.done ? "rec-completed" : "";
                    const disabled = !isMe ? "disabled" : "";
                    const delBtn = isMe ? `<button class="rec-btn rec-btn-del" onclick="removeRecTodo(this)">삭제</button>` : "";
                    li.innerHTML = `
                        <label><input type="checkbox" onchange="toggleRecTodo(this)" ${checked} ${disabled}> <span class="${compClass}">${todo.text}</span></label>
                        <div><span class="rec-time-tag">${todo.time || ''}</span> ${delBtn}</div>
                    `;
                    ul.appendChild(li);
                });

                buildRecordCalendar(nickname, data.calendar || {});
                checkGoalAchievement();
            }

            function buildRecordCalendar(nickname, calendarData) {
                const now = new Date();
                const y = window.recViewYear;
                const m = window.recViewMonth;
                const today = (y === now.getFullYear() && m === now.getMonth() + 1) ? now.getDate() : -1;
                const monthStr = `${y}-${String(m).padStart(2, '0')}`;
                
                document.getElementById('rec-cal-month-title').innerHTML = `
                    <div style="display:flex; justify-content:space-between; width:100%; align-items:center;">
                        <span>
                            <button class="rec-btn" onclick="changeRecMonth(-1, '${nickname}')" style="padding: 2px 6px;">◀️</button>
                            📅 ${y}년 ${m}월 집필 통계
                            <button class="rec-btn" onclick="changeRecMonth(1, '${nickname}')" style="padding: 2px 6px;">▶️</button>
                        </span>
                        <button class="rec-btn" onclick="toggleRecCalendar()" id="rec-cal-toggle-btn" style="font-size: 11px; padding: 3px 8px;">접기 🔼</button>
                    </div>
                `;
                
                const grid = document.getElementById('rec-calendar-grid');
                let html = '';
                const daysOfWeek = ['일','월','화','수','목','금','토']; 
                daysOfWeek.forEach(d => { html += `<div class="rec-cal-header">${d}</div>`; });
                
                const firstDay = new Date(y, m - 1, 1).getDay(); 
                const daysInMonth = new Date(y, m, 0).getDate(); 
                
                for(let i=0; i<firstDay; i++) { html += `<div class="rec-cal-day" style="background:transparent; border:none;"></div>`; }
                
                const monthData = calendarData[monthStr] || {};
                
                for(let i=1; i<=daysInMonth; i++) {
                    const dayData = monthData[i.toString()];
                    let isDone = false;
                    let inner = `<span class="date-num">${i}일${i===today?'(오늘)':''}</span>`;
                    
                    if (dayData) {
                        isDone = (parseInt(dayData.done) >= parseInt(dayData.target)) && parseInt(dayData.target) > 0;
                        inner += `<span class="goal">목표:${Number(dayData.target).toLocaleString()}</span>`;
                        inner += `<span class="act" ${isDone?'style="font-weight:bold;"':''}>완료:${Number(dayData.done).toLocaleString()}</span>`;
                        
                        if (dayData.seconds !== undefined) {
                            let h = Math.floor(dayData.seconds / 3600);
                            let min = Math.floor((dayData.seconds % 3600) / 60);
                            let s = dayData.seconds % 60;
                            let timeStr = `${String(h).padStart(2,'0')}:${String(min).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
                            inner += `<span class="time">⏱️${timeStr}</span>`;
                        }
                    }
                    
                    let cls = "rec-cal-day";
                    if (isDone) cls += " done";
                    if (i === today) {
                        html += `<div class="${cls}" style="border: 2px solid var(--rec-primary);">${inner}</div>`;
                    } else {
                        html += `<div class="${cls}">${inner}</div>`;
                    }
                }
                grid.innerHTML = html;
            }

            function loadMyLocalTrackerData() {
                if (!window.trackersData) window.trackersData = {};
                if (!window.trackersData[window.myNickname]) window.trackersData[window.myNickname] = { calendar: {}, todos: [], lastDate: "" };
                
                const now = new Date();
                const todayStr = `${now.getFullYear()}-${now.getMonth()+1}-${now.getDate()}`;
                let savedLastDate = localStorage.getItem('lastDate');

                if (localStorage.getItem('targetChars')) { window.trackersData[window.myNickname].targetChars = localStorage.getItem('targetChars'); }
                if (localStorage.getItem('themeColor')) { window.trackersData[window.myNickname].themeColor = localStorage.getItem('themeColor'); }

                if (savedLastDate && savedLastDate !== todayStr) {
                    window.trackersData[window.myNickname].doneChars = 0;
                    window.trackersData[window.myNickname].todos = [];
                    window.trackersData[window.myNickname].lastDate = todayStr;
                    localStorage.setItem('doneChars', '0');
                    localStorage.setItem('myTodos', '[]');
                    localStorage.setItem('lastDate', todayStr);
                    recTotalSeconds = 0;
                } else {
                    if (localStorage.getItem('doneChars')) { window.trackersData[window.myNickname].doneChars = localStorage.getItem('doneChars'); }
                    if (localStorage.getItem('myTodos')) { 
                        try { window.trackersData[window.myNickname].todos = JSON.parse(localStorage.getItem('myTodos')); } 
                        catch(e) { window.trackersData[window.myNickname].todos = []; }
                    }
                    window.trackersData[window.myNickname].lastDate = todayStr;
                }
                
                if(window.myNickname) {
                    saveRecordData(true); 
                }
            }

            function checkGoalAchievement() {
                const target = parseInt(document.getElementById('rec-target-chars').value) || 0;
                const done = parseInt(document.getElementById('rec-done-chars').value) || 0;
                const banner = document.getElementById('rec-congrats-banner');
                const isMe = (window.currentViewingUser === window.myNickname);

                if (done >= target && target > 0) {
                    banner.style.display = 'block';
                    banner.innerText = isMe ? "🎉 축하합니다! 오늘의 목표 분량을 모두 달성했습니다!" : `🎉 우와! ${window.currentViewingUser} 작가님이 오늘의 목표를 달성했습니다!`;
                    
                    if (isMe && !window.goalAchieved) {
                        window.goalAchieved = true;
                        if (ws && ws.readyState === WebSocket.OPEN && window.myNickname) {
                            ws.send(JSON.stringify({ type: "chat", senderName: "🎉시스템", msg: `🎊 ${window.myNickname} 작가님이 오늘의 목표 분량을 모두 완료했습니다! 축하해주세요! 🎊` }));
                        }
                    }
                } else {
                    banner.style.display = 'none';
                    if (isMe) window.goalAchieved = false;
                }
            }

            function saveRecordData(isSilent = false) {
                if (!window.myNickname) return;
                
                const now = new Date();
                const todayStr = `${now.getFullYear()}-${now.getMonth()+1}-${now.getDate()}`;

                if (!window.trackersData) window.trackersData = {};
                if (!window.trackersData[window.myNickname]) window.trackersData[window.myNickname] = { calendar: {}, todos: [], lastDate: todayStr };
                let myData = window.trackersData[window.myNickname];

                if (myData.lastDate && myData.lastDate !== todayStr) {
                     document.getElementById('rec-done-chars').value = 0;
                     document.getElementById('rec-todo-list').innerHTML = '';
                     recTotalSeconds = 0;
                     updateMainTimerDisplay();
                     myData.lastDate = todayStr;
                     myData.doneChars = 0;
                     myData.todos = [];
                     localStorage.setItem('doneChars', '0');
                     localStorage.setItem('myTodos', '[]');
                     localStorage.setItem('lastDate', todayStr);
                     alert("자정이 지나 날짜가 변경되었습니다! 오늘의 완료량과 할 일이 자동으로 리셋되었습니다. 🌱");
                }

                const target = document.getElementById('rec-target-chars').value;
                const done = document.getElementById('rec-done-chars').value;
                const color = document.getElementById('themeColorPicker').value;
                const totalSecs = recTotalSeconds;

                localStorage.setItem('targetChars', target);
                localStorage.setItem('doneChars', done);
                localStorage.setItem('themeColor', color);
                localStorage.setItem('lastDate', todayStr);
                
                const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
                const dayStr = now.getDate().toString();
                
                myData.targetChars = target;
                myData.doneChars = done;
                myData.themeColor = color;
                myData.lastDate = todayStr;

                const ul = document.getElementById('rec-todo-list');
                const lis = ul.querySelectorAll('li');
                let currentTodos = [];
                lis.forEach(li => {
                    const textEl = li.querySelector('label span');
                    if(!textEl) return;
                    const text = textEl.innerText;
                    const doneBox = li.querySelector('input[type="checkbox"]');
                    const isDone = doneBox ? doneBox.checked : false;
                    const timeTag = li.querySelector('.rec-time-tag');
                    const timeStr = timeTag ? timeTag.innerText : '';
                    currentTodos.push({ text: text, done: isDone, time: timeStr });
                });
                myData.todos = currentTodos;
                localStorage.setItem('myTodos', JSON.stringify(currentTodos));

                if (!myData.calendar) myData.calendar = {};
                if (!myData.calendar[monthStr]) myData.calendar[monthStr] = {};
                myData.calendar[monthStr][dayStr] = { target: target, done: done, seconds: totalSecs };

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "tracker_update", nickname: window.myNickname, tracker_data: myData }));
                }

                buildRecordCalendar(window.myNickname, myData.calendar);
                checkGoalAchievement();

                if (!isSilent) {
                    alert('✨ 목표, 완료 글자수와 집필 시간, 오늘의 할 일까지 서버에 안전하게 저장되었습니다! 💾');
                }
            }

            function changeThemeColor(hex) {
                const root = document.documentElement;
                root.style.setProperty('--rec-primary', hex);
                root.style.setProperty('--rec-border', hex);
                root.style.setProperty('--rec-bg', '#ffffff');
                root.style.setProperty('--rec-box', '#ffffff');
                root.style.setProperty('--rec-sec', hex + '10');
                root.style.setProperty('--rec-done', hex + '30');
            }

            function updateMainTimerDisplay() {
                const hrs = String(Math.floor(recTotalSeconds / 3600)).padStart(2, '0');
                const mins = String(Math.floor((recTotalSeconds % 3600) / 60)).padStart(2, '0');
                const secs = String(recTotalSeconds % 60).padStart(2, '0');
                document.getElementById('rec-main-timer').textContent = `${hrs}:${mins}:${secs}`;
            }

            function startMainTimer() {
                if (recMainTimerInterval) return;
                recMainTimerInterval = setInterval(() => { recTotalSeconds++; updateMainTimerDisplay(); }, 1000);
            }

            function pauseMainTimer() {
                clearInterval(recMainTimerInterval);
                recMainTimerInterval = null;
            }

            function resetMainTimer() {
                pauseMainTimer();
                recTotalSeconds = 0;
                updateMainTimerDisplay();
            }

            function startPomodoro() {
                if (recPomoInterval) return;
                const workMins = parseInt(document.getElementById('rec-pomo-work').value) || 25;
                if (recPomoSeconds === 25 * 60) recPomoSeconds = workMins * 60;
                startMainTimer();
                recPomoInterval = setInterval(() => {
                    if (recPomoSeconds > 0) {
                        recPomoSeconds--;
                        const mins = String(Math.floor(recPomoSeconds / 60)).padStart(2, '0');
                        const secs = String(recPomoSeconds % 60).padStart(2, '0');
                        const status = recIsWorking ? "(집필 중 ✍️)" : "(휴식 중 ☕)";
                        document.getElementById('rec-pomo-display').textContent = `${mins}:${secs} ${status}`;
                    } else {
                        playBipSound();
                        if (recIsWorking) {
                            recIsWorking = false;
                            const restMins = parseInt(document.getElementById('rec-pomo-rest').value) || 5;
                            recPomoSeconds = restMins * 60;
                            alert('집필 시간 종료! 휴식 시작 ☕');
                        } else {
                            recIsWorking = true;
                            const workMins = parseInt(document.getElementById('rec-pomo-work').value) || 25;
                            recPomoSeconds = workMins * 60;
                            alert('휴식 시간이 종료되었습니다! 다시 집필 시작 ✍️');
                        }
                    }
                }, 1000);
            }

            function pausePomodoro() {
                clearInterval(recPomoInterval);
                recPomoInterval = null;
                pauseMainTimer();
            }

            function resetPomodoro() {
                pausePomodoro();
                const workMins = parseInt(document.getElementById('rec-pomo-work').value) || 25;
                recPomoSeconds = workMins * 60;
                recIsWorking = true;
                document.getElementById('rec-pomo-display').textContent = "25:00 (대기 중)";
            }

            function toggleRecCalendar() {
                const wrap = document.getElementById('rec-calendar-wrap');
                const btn = document.getElementById('rec-cal-toggle-btn');
                if (wrap.style.display === 'block') {
                    wrap.style.display = 'none';
                    btn.textContent = '펼치기 🔽';
                } else {
                    wrap.style.display = 'block';
                    btn.textContent = '접기 🔼';
                }
            }

            function addRecTodo() {
                const input = document.getElementById('rec-new-todo-text');
                const text = input.value.trim();
                if (!text) return;
                const ul = document.getElementById('rec-todo-list');
                const li = document.createElement('li');
                li.innerHTML = `
                    <label><input type="checkbox" onchange="toggleRecTodo(this)"> <span>${text}</span></label>
                    <div><span class="rec-time-tag"></span> <button class="rec-btn rec-btn-del" onclick="removeRecTodo(this)">삭제</button></div>
                `;
                ul.appendChild(li);
                input.value = '';
                saveRecordData(true);
            }

            function removeRecTodo(btn) { 
                btn.closest('li').remove(); 
                saveRecordData(true);
            }

            function toggleRecTodo(checkbox) {
                const li = checkbox.closest('li');
                const span = li.querySelector('label span');
                const timeTag = li.querySelector('.rec-time-tag');
                if (checkbox.checked) {
                    span.classList.add('rec-completed');
                    const now = new Date();
                    timeTag.textContent = `(완료: ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')})`;
                } else {
                    span.classList.remove('rec-completed');
                    timeTag.textContent = '';
                }
                saveRecordData(true);
            }

            function playBipSound() {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                let beepCount = 0;
                const interval = setInterval(() => {
                    if (beepCount >= 4) { clearInterval(interval); return; }
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();
                    osc.type = 'sine';
                    osc.frequency.setValueAtTime(880, audioCtx.currentTime);
                    osc.connect(gain);
                    gain.connect(audioCtx.destination);
                    osc.start();
                    osc.stop(audioCtx.currentTime + 0.15);
                    beepCount++;
                }, 200);
            }

            function changeRecMonth(offset, nickname) {
                window.recViewMonth += offset;
                if (window.recViewMonth < 1) { window.recViewMonth = 12; window.recViewYear--; }
                else if (window.recViewMonth > 12) { window.recViewMonth = 1; window.recViewYear++; }
                
                const data = window.trackersData[nickname] || { calendar: {} };
                buildRecordCalendar(nickname, data.calendar || {});
            }

            checkLogin();
        </script>
    </body>
    </html>
    """

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    client_id = str(id(websocket))
    
    try:
        await websocket.send_text(json.dumps({"type": "welcome", "clientId": client_id}))
        await websocket.send_text(json.dumps({"type": "init_state", "state": server_state}))
        
        while True:
            data = await websocket.receive_text()
            packet = json.loads(data)
            p_type = packet.get("type")

            if p_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if p_type == "set_nickname":
                nickname = packet.get("nickname", "익명")
                owned = packet.get("owned", [])
                
                to_close = []
                for existing_ws, name in list(manager.active_users.items()):
                    if name == nickname and existing_ws != websocket:
                        to_close.append(existing_ws)
                
                for old_ws in to_close:
                    try:
                        await old_ws.send_text(json.dumps({"type": "duplicate_kicked"}))
                        await old_ws.close()
                    except:
                        pass

                manager.active_users[websocket] = nickname
                await manager.broadcast_user_list()
                if client_id not in manager.active_slots:
                    manager.active_slots[client_id] = []
                log_entry = {"msg": f"{nickname} 님이 입장했습니다.", "time": __import__('time').time()}
                server_state.setdefault("admin_log", []).append(log_entry)
                if len(server_state["admin_log"]) > 100: server_state["admin_log"].pop(0)
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                await manager.broadcast(json.dumps({"type": "admin_log_update", "log": log_entry}))
                
                recovered = False
                
                if owned:
                    for idx in owned:
                        if 0 <= idx < 16:
                            current_user = server_state["cards"][idx]["user"]
                            if current_user.startswith("자리") or current_user == nickname:
                                if idx not in manager.active_slots[client_id]:
                                    manager.active_slots[client_id].append(idx)
                                server_state["cards"][idx]["user"] = nickname
                                recovered = True
                                change_packet = json.dumps({"type": "username_change", "index": idx, "user": nickname})
                                await manager.broadcast(change_packet)
                                await websocket.send_text(change_packet)
                
                for i, card in enumerate(server_state["cards"]):
                    if card["user"] == nickname:
                        if i not in manager.active_slots[client_id]:
                            manager.active_slots[client_id].append(i)
                            recovered = True
                            change_packet = json.dumps({"type": "username_change", "index": i, "user": nickname})
                            await manager.broadcast(change_packet)
                            await websocket.send_text(change_packet)

                if recovered:
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                    continue

                assigned_idx = None
                for i, card in enumerate(server_state["cards"]):
                    if card["user"].startswith("자리"):
                        assigned_idx = i
                        break
                if assigned_idx is not None:
                    manager.active_slots[client_id].append(assigned_idx)
                    server_state["cards"][assigned_idx]["user"] = nickname
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                    change_packet = json.dumps({"type": "username_change", "index": assigned_idx, "user": nickname})
                    await manager.broadcast(change_packet)
                    await websocket.send_text(change_packet)
                continue

            if p_type == "clear_chat":
                server_state["chat_history"] = []
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                await manager.broadcast(json.dumps({"type": "chat_cleared"}))
                await websocket.send_text(json.dumps({"type": "chat_cleared"}))
                continue

            if p_type == "kick":
                target_nick = packet.get("target_nick")
                for ws_conn, name in list(manager.active_users.items()):
                    if name == target_nick:
                        try: await ws_conn.send_text(json.dumps({"type": "kicked"}))
                        except: pass
                continue

            if p_type == "chat":
                chat_obj = {"senderName": packet.get("senderName"), "msg": packet.get("msg"), "time": packet.get("time", "")}
                server_state["chat_history"].append(chat_obj)
                if len(server_state["chat_history"]) > 100: server_state["chat_history"].pop(0)
                await manager.broadcast(json.dumps(packet))
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                
            elif p_type == "attendance":
                month = packet.get("month")
                day = packet.get("day")
                nickname = packet.get("nickname")
                if "attendance" not in server_state: server_state["attendance"] = {}
                if month not in server_state["attendance"]: server_state["attendance"][month] = {}
                if nickname not in server_state["attendance"][month]: server_state["attendance"][month][nickname] = []
                if day not in server_state["attendance"][month][nickname]:
                    server_state["attendance"][month][nickname].append(day)
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                await manager.broadcast(json.dumps({"type": "attendance_update", "attendance": server_state["attendance"]}))
                
            elif p_type == "tracker_update":
                nickname = packet.get("nickname")
                if "trackers" not in server_state: server_state["trackers"] = {}
                server_state["trackers"][nickname] = packet.get("tracker_data")
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                await manager.broadcast(json.dumps({"type": "tracker_update", "nickname": nickname, "tracker_data": packet.get("tracker_data")}), exclude=websocket)
                
            else:
                packet["sender"] = client_id
                if p_type == "username_change":
                    idx = packet["index"]
                    val = packet["user"]
                    server_state["cards"][idx]["user"] = val
                    if not val.startswith("자리"):
                        if client_id not in manager.active_slots: manager.active_slots[client_id] = []
                        if idx not in manager.active_slots[client_id]: manager.active_slots[client_id].append(idx)
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                elif p_type == "card_bg_change":
                    server_state["cards"][packet["index"]]["card_bg"] = packet.get("dataUrl")
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                elif p_type == "toggle_mosaic":
                    server_state["cards"][packet["index"]]["is_mosaic"] = packet.get("is_mosaic", False)
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                elif p_type == "start_share":
                    manager.active_shares[packet["index"]] = client_id
                elif p_type == "stop_share":
                    idx = packet.get("index")
                    if idx in manager.active_shares: del manager.active_shares[idx]
                elif p_type == "update_notice":
                    server_state["global_notice"] = packet.get("notice", "")
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                elif p_type == "status_update":
                    server_state["cards"][packet["index"]]["status"] = packet.get("status", 0)
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                
                await manager.broadcast(json.dumps(packet), exclude=websocket)

    except (WebSocketDisconnect, Exception):
        pass

    finally:
        client_id = str(id(websocket))
        nickname = manager.active_users.get(websocket, "")
        
        if client_id in manager.active_slots:
            del manager.active_slots[client_id]
            asyncio.create_task(asyncio.to_thread(save_data, server_state))

        freed_indexes = manager.disconnect(websocket)
        await manager.broadcast_user_list()
        
        if nickname and nickname != "연결중...":
            log_entry = {"msg": f"{nickname} 님이 잠시 튕겼거나 퇴장했습니다.", "time": __import__('time').time()}
            server_state.setdefault("admin_log", []).append(log_entry)
            if len(server_state["admin_log"]) > 100: server_state["admin_log"].pop(0)
            asyncio.create_task(asyncio.to_thread(save_data, server_state))
            await manager.broadcast(json.dumps({"type": "admin_log_update", "log": log_entry}))
        
        for idx in freed_indexes:
            await manager.broadcast(json.dumps({"type": "stop_share", "index": idx}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
