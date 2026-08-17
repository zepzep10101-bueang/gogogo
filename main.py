from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
json = __import__('json')
os = __import__('os')
uvicorn = __import__('uvicorn')
asyncio = __import__('asyncio')

from pymongo import MongoClient

MONGO_URI = "mongodb+srv://zepzep10101_db_user:9zT7ZAjz5tcQe2dX@cluster0.sai0kyf.mongodb.net/?appName=Cluster0"
try:
    client = MongoClient(MONGO_URI)
    db = client["dashboard_db"]
    collection = db["dashboard_data"]
except Exception as e:
    print("망고로드 연결 실패:", e)

def load_data():
    try:
        data = collection.find_one({"_id": "main_state"})
        if data:
            cards = data.get("cards", [])
            if len(cards) > 16:
                data["cards"] = cards[:16]
            elif len(cards) < 16:
                new_cards = [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": 0, "timer_visible": False, "timer_running": False, "timer_elapsed": 0, "timer_last_start": 0} for i in range(len(cards), 16)]
                data["cards"].extend(new_cards)
            
            for i, card in enumerate(data["cards"]):
                card["user"] = card.get("user") or f"자리{i+1}"
                card["is_mosaic"] = card.get("is_mosaic", False)
                card["is_large"] = card.get("is_large", False)
                card["status"] = card.get("status", 0)
                card["timer_visible"] = card.get("timer_visible", False)
                card["timer_running"] = card.get("timer_running", False)
                card["timer_elapsed"] = card.get("timer_elapsed", 0)
                card["timer_last_start"] = card.get("timer_last_start", 0)
            
            if "global_notice" not in data:
                data["global_notice"] = "📌 다 함께 모여서 열심히 마감해 봅시다!"
            
            if "attendance" not in data:
                data["attendance"] = {}
                
            if "admin_log" not in data:
                data["admin_log"] = []
                
            return data
    except Exception:
        pass
    
    initial_data = {
        "_id": "main_state",
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": 0, "timer_visible": False, "timer_running": False, "timer_elapsed": 0, "timer_last_start": 0} for i in range(16)],
        "chat_history": [],
        "global_notice": "📌 다 함께 모여서 열심히 마감해 봅시다!",
        "attendance": {},
        "admin_log": []
    }
    collection.update_one({"_id": "main_state"}, {"$set": initial_data}, upsert=True)
    return initial_data

def save_data(data):
    try:
        collection.update_one({"_id": "main_state"}, {"$set": data}, upsert=True)
    except Exception as e:
        print("망고로드 저장 에러:", e)

server_state = load_data()
app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.active_shares: dict[int, str] = {}
        self.active_users: dict[WebSocket, str] = {}
        self.active_slots: dict[str, list[int]] = {} 

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.active_users[websocket] = "연결중..."

    def disconnect(self, websocket: WebSocket):
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

    async def broadcast(self, message: str, exclude: WebSocket = None):
        dead_connections = []
        for connection in list(self.active_connections):
            if connection != exclude:
                try:
                    await connection.send_text(message)
                except Exception:
                    dead_connections.append(connection)
        
        for dead in dead_connections:
            freed_indexes = self.disconnect(dead)
            await self.broadcast_user_list()
            for conn in self.active_connections:
                try:
                    for idx in freed_indexes:
                        await conn.send_text(json.dumps({"type": "stop_share", "index": idx}))
                except Exception:
                    pass

    async def broadcast_user_list(self):
        users_info = [{"clientId": str(id(ws)), "nickname": name} for ws, name in self.active_users.items() if name != "연결중..."]
        msg = json.dumps({"type": "user_list", "count": len(self.active_connections), "users": users_info})
        for conn in self.active_connections:
            try:
                await conn.send_text(msg)
            except Exception:
                pass

manager = ConnectionManager()

# [추가] 외부에서 인원수만 가볍게 물어보는 API 창구
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
            #bgMediaWrapper img { width: 100vw; height: 100vh; object-fit: cover; display: block; }
            #bgMediaWrapper iframe { width: 100vw; height: 100vh; pointer-events: none; border: none; }
            
            .overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.05); z-index: 1; pointer-events: none; }

            .main-container { display: grid; grid-template-columns: minmax(0, 1fr) 240px; gap: 15px; padding: 15px; min-height: 100vh; color: white; position: relative; z-index: 2; align-items: start; max-width: 1800px; margin: 0 auto; }
            
            .card-grid { display: grid; gap: 10px; grid-template-columns: repeat(4, minmax(0, 1fr)); grid-auto-flow: dense; width: 100%; align-content: start; }
            @media (max-width: 1300px) { .card-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
            @media (max-width: 950px) { .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
            @media (max-width: 600px) { .card-grid { grid-template-columns: repeat(1, minmax(0, 1fr)); } }
            
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); backdrop-filter: blur(5px); min-height: 250px; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: all 0.3s ease; }
            .card-large { grid-column: span 2; grid-row: span 2; min-height: 510px; }

            .card-header { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 20; width: 100%; }
            .btn-group { display: flex; gap: 2px; width: 100%; justify-content: center; flex-wrap: nowrap; overflow: visible; }
            .share-btn { padding: 4px 2px; font-size: 10px; color: white; border: none; border-radius: 3px; cursor: pointer; white-space: nowrap; font-weight: bold; text-align: center; flex-grow: 1; }

            .card-stream-box { width: 100%; flex-grow: 1; min-height: 135px; background: rgba(0, 0, 0, 0.15); border-radius: 8px; overflow: hidden; position: relative; margin-top: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 2; pointer-events: none; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: transparent; position: absolute; top: 0; left: 0; z-index: 10; transition: filter 0.2s ease-in-out; pointer-events: auto; }

            .side-panel { display: flex; flex-direction: column; gap: 15px; position: sticky; top: 15px; height: calc(100vh - 30px); min-width: 0; }
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 12px; border: 1px solid rgba(255, 255, 255, 0.2); backdrop-filter: blur(5px); min-width: 0; word-break: keep-all; overflow-wrap: break-word; }
            
            .settings-toggle-btn { border: none; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: bold; white-space: nowrap; }

            .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0, 0, 0, 0.7); z-index: 10000; align-items: center; justify-content: center; }
            .modal-box { background: rgba(30, 30, 40, 0.95); border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 12px; padding: 20px; width: 350px; max-height: 80vh; overflow-y: auto; color: white; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.5); backdrop-filter: blur(5px); }
            .close-btn { position: absolute; top: 12px; right: 15px; background: transparent; border: none; color: white; font-size: 14px; cursor: pointer; font-weight: bold; transition: 0.2s; }
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
        </style>
    </head>
    <body>

        <script>
            document.addEventListener('contextmenu', function(e) { e.preventDefault(); });
            document.addEventListener('keydown', function(e) {
                if (e.keyCode === 123 || (e.ctrlKey && e.shiftKey && (e.keyCode === 73 || e.keyCode === 74 || e.keyCode === 67)) || (e.ctrlKey && e.keyCode === 85)) {
                    e.preventDefault();
                    return false;
                }
            });
            document.addEventListener('wheel', function(e) {
                if (e.ctrlKey) { e.preventDefault(); }
            }, { passive: false });
            document.addEventListener('touchstart', function(e) {
                if (e.touches.length > 1) { e.preventDefault(); }
            }, { passive: false });
        </script>

        <div class="login-overlay" id="loginOverlay">
            <div class="login-box">
                <h2>🔒 행운방 입장</h2>
                <p style="font-size: 13px; color: #aaa; margin-top: 5px; margin-bottom: 10px;">닉네임은 한 번만 적으면 저장 돼!</p>
                
                <div id="loginUserCount" style="margin-bottom: 15px; font-size: 14px; font-weight: bold; color: #00b894; background: rgba(0, 184, 148, 0.15); padding: 8px; border-radius: 6px; border: 1px solid rgba(0, 184, 148, 0.4);">
                    🔥 현재 달리고 있는 작가님: 확인 중...
                </div>

                <input type="text" id="nickInput" placeholder="내 닉네임 (예: 부엉)" onkeypress="if(event.key==='Enter') login()"><br>
                <input type="password" id="pwInput" placeholder="비밀번호" onkeypress="if(event.key==='Enter') login()">
                <br>
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
                <div id="adminLogContent" style="background: rgba(0,0,0,0.5); padding: 10px; border-radius: 8px; height: 250px; overflow-y: auto; font-size: 12px; color: #ddd; line-height: 1.6;">
                    기록이 없습니다.
                </div>
            </div>
        </div>

        <div class="video-background" id="bgContainer">
            <div id="bgMediaWrapper"></div>
        </div>
        <div class="overlay"></div>

        <div class="main-container">
            <div class="card-grid" id="cardGrid"></div>

            <div class="side-panel">
                <div class="panel-box" style="flex-shrink: 0;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 4px;">
                        <h3 style="margin: 0; font-size: 15px; line-height: 20px;">👑 대시보드</h3>
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

            // [추가] 로그인 화면에 인원수를 주기적으로 업데이트하는 가벼운 함수
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

            // 페이지 로드 시 바로 한 번 인원수 체크
            updateLoginUserCount();

            // 로그인 화면이 떠 있는 동안 10초마다 인원수를 가볍게 새로고침 (렉 제로!)
            let countInterval = setInterval(() => {
                if(document.getElementById('loginOverlay').style.display !== 'none') {
                    updateLoginUserCount();
                } else {
                    clearInterval(countInterval); // 방에 들어가면 더 이상 안 물어보고 멈춤
                }
            }, 10000);

            function openModal(modalId) {
                document.getElementById(modalId).style.display = 'flex';
                if (modalId === 'attendanceModal') { renderAttendanceBoard(); }
                if (modalId === 'adminLogModal') { renderAdminLog(); }
            }
