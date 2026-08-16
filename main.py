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
                new_cards = [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": 0} for i in range(len(cards), 16)]
                data["cards"].extend(new_cards)
            
            for i, card in enumerate(data["cards"]):
                card["user"] = f"자리{i+1}"
                card["is_mosaic"] = False
                if "is_large" not in card:
                    card["is_large"] = False
                if "status" not in card:
                    card["status"] = 0
                if "work_start_time" not in card:
                    card["work_start_time"] = 0
            
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
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": 0, "work_start_time": 0} for i in range(16)],
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

@app.get("/", response_class=HTMLResponse)
def read_root():
    return r"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>🍀심사 합격 & 돈 긁어모으는 방🏆</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Arial', sans-serif; }
            body, html { width: 100%; height: 100%; overflow-x: hidden; overflow-y: auto; background: #111; }

            .login-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #111; z-index: 9999; display: flex; align-items: center; justify-content: center; flex-direction: column; }
            .login-box { background: rgba(30, 30, 40, 0.9); padding: 30px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.2); text-align: center; color: white; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
            .login-box input { padding: 10px; margin-top: 10px; border-radius: 5px; border: none; width: 220px; text-align: center; }
            .login-box button { padding: 10px 20px; margin-top: 15px; border: none; border-radius: 5px; background: #ff7675; color: white; cursor: pointer; font-weight: bold; width: 100%; }

            .video-background { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; overflow: hidden; pointer-events: none; background: #000; }
            #bgMediaWrapper { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; }
            #bgMediaWrapper img { width: 100vw; height: 100vh; object-fit: cover; display: block; }
            #bgMediaWrapper iframe { width: 100vw; height: 100vh; pointer-events: none; border: none; }
            
            .overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.05); z-index: 1; pointer-events: none; }

            .main-container { display: grid; grid-template-columns: 5fr 240px; gap: 20px; padding: 20px; min-height: 100vh; color: white; position: relative; z-index: 2; align-items: start; max-width: 1800px; margin: 0 auto; min-width: 0; }
            
            /* [수정] auto-fit 적용! 남는 공간 없이 화면에 꽉 차게 알아서 늘어남! (군대식 4칸 고정 삭제) */
            .card-grid { display: grid; gap: 15px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); grid-auto-flow: dense; width: 100%; align-content: start; min-width: 0; }
            
            /* [수정] 억지 4:3 비율 삭제! 내용물에 맞게 자연스럽게 커짐 */
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 12px; padding: 8px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); backdrop-filter: blur(5px); min-height: 0; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: all 0.3s ease; }
            
            /* [유지] '크게' 상태일 때 2칸 차지 */
            .card-large { grid-column: span 2; grid-row: span 2; }

            .card-header { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 20; width: 100%; }
            .btn-group { display: flex; gap: 2px; width: 100%; justify-content: center; flex-wrap: nowrap; overflow: visible; }
            .share-btn { padding: 4px 2px; font-size: 10px; color: white; border: none; border-radius: 3px; cursor: pointer; white-space: nowrap; font-weight: bold; text-align: center; flex-grow: 1; }

            /* [수정] 까만 화공 화면에만 16:9 황금 비율 적용! 어색한 빈 공간 싹 제거! */
            .card-stream-box { width: 100%; flex-grow: 1; aspect-ratio: 16 / 9; min-height: 120px; max-height: calc(100vh - 120px); background: rgba(0, 0, 0, 0.4); border-radius: 8px; overflow: hidden; position: relative; margin-top: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 2; pointer-events: none; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: transparent; position: absolute; top: 0; left: 0; z-index: 10; transition: filter 0.2s ease-in-out; pointer-events: auto; }

            .side-panel { display: flex; flex-direction: column; gap: 15px; position: sticky; top: 20px; height: calc(100vh - 40px); min-width: 0; }
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

        <!-- 로그인 모달 -->
        <div class="login-overlay" id="loginOverlay">
            <div class="login-box">
                <h2>🔒 행운방 입장</h2>
                <p style="font-size: 13px; color: #aaa; margin-top: 5px; margin-bottom: 15px;">닉네임은 한 번만 적으면 저장 돼!</p>
                <input type="text" id="nickInput" placeholder="내 닉네임 (예: 부엉)" onkeypress="if(event.key==='Enter') login()"><br>
                <input type="password" id="pwInput" placeholder="비밀번호" onkeypress="if(event.key==='Enter') login()">
                <br>
                <button onclick="login()">입장하기</button>
            </div>
        </div>

        <!-- 공지사항 모달 -->
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

        <!-- 출석현황 모달 -->
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

        <!-- 배경설정 모달 -->
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

        <!-- 방장 전용 출입 기록 모달 -->
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

            function openModal(modalId) {
                document.getElementById(modalId).style.display = 'flex';
                if (modalId === 'attendanceModal') {
                    renderAttendanceBoard();
                }
                if (modalId === 'adminLogModal') {
                    renderAdminLog();
                }
            }
            function closeModal(modalId) {
                document.getElementById(modalId).style.display = 'none';
            }

            function formatLogTime(ts) {
                const now = new Date(ts * 1000);
                const m = now.getMonth() + 1;
                const d = now.getDate();
                const timeString = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
                return `${m}/${d} ${timeString}`;
            }

            function renderAdminLog() {
                const container = document.getElementById('adminLogContent');
                if (!window.adminLogData || window.adminLogData.length === 0) {
                    container.innerHTML = "기록이 없습니다.";
                    return;
                }
                container.innerHTML = window.adminLogData.map(log => {
                    return `<div style="margin-bottom: 4px;">[${formatLogTime(log.time)}] <b style="color:#ffeaa7;">${log.msg}</b></div>`;
                }).join("");
                container.scrollTop = container.scrollHeight;
            }

            function renderAttendanceBoard() {
                const now = new Date();
                const y = now.getFullYear();
                const m = now.getMonth() + 1;
                const today = now.getDate();
                const monthStr = `${y}-${String(m).padStart(2, '0')}`;
                
                const calTitleEl = document.getElementById('calMonthTitle');
                if (calTitleEl) calTitleEl.innerText = `🍀 ${y}년 ${m}월 내 출석부`;
                
                const grid = document.getElementById('calendarGrid');
                if (grid) {
                    const firstDay = new Date(y, m - 1, 1).getDay();
                    const daysInMonth = new Date(y, m, 0).getDate();
                    
                    let html = '';
                    const daysOfWeek = ['일','월','화','수','목','금','토'];
                    daysOfWeek.forEach(d => { 
                        html += `<div style="text-align:center; font-size:10px; color:#ffeaa7; margin-bottom: 4px;">${d}</div>`; 
                    });
                    
                    for(let i=0; i<firstDay; i++) {
                        html += `<div></div>`;
                    }
                    
                    const myName = window.myNickname || "익명";
                    const myAtt = (window.attendanceData[monthStr] && window.attendanceData[monthStr][myName]) || [];
                    
                    for(let i=1; i<=daysInMonth; i++) {
                        const isToday = (i === today);
                        const isStamped = myAtt.includes(i);
                        
                        let cls = "cal-day";
                        if (isToday && !isStamped) cls += " today";
                        if (isStamped) cls += " stamped";
                        
                        const content = isStamped ? '🍀' : i;
                        
                        if (isToday && !isStamped) {
                            html += `<div class="${cls}" onclick="stampAttendance(${y}, ${m}, ${i})" title="오늘 출석 도장 찍기!">${content}</div>`;
                        } else {
                            html += `<div class="${cls}">${content}</div>`;
                        }
                    }
                    grid.innerHTML = html;
                }

                const titleEl = document.getElementById('rankTitle');
                if (titleEl) titleEl.innerText = `🏆 ${m}월 모두의 랭킹`;

                const monthData = window.attendanceData[monthStr] || {};
                let rankArr = [];
                let todayAttendees = [];
                
                for (let user in monthData) {
                    const stamps = monthData[user];
                    rankArr.push({ name: user, count: stamps.length });
                    if (stamps.includes(today)) {
                        todayAttendees.push(user);
                    }
                }
                
                rankArr.sort((a, b) => b.count - a.count);
                
                let rankHtml = '';
                if (rankArr.length === 0) {
                    rankHtml = '아직 이번 달 출석한 사람이 없어!';
                } else {
                    rankArr.forEach((item, idx) => {
                        let medal = '🏅';
                        if (idx === 0) medal = '🥇';
                        else if (idx === 1) medal = '🥈';
                        else if (idx === 2) medal = '🥉';
                        
                        let emphasis = (idx < 3) ? 'font-weight:bold; color:#fff;' : '';
                        rankHtml += `<div style="${emphasis} margin-bottom: 4px;">${medal} ${item.name} : ${item.count}일</div>`;
                    });
                }
                
                let todayHtml = '';
                if (todayAttendees.length === 0) {
                    todayHtml = '아직 오늘 출석한 사람이 없어! 빨리 1빠 찍어!';
                } else {
                    todayHtml = todayAttendees.map(u => `<span style="background:rgba(39, 174, 96, 0.6); padding:4px 8px; border-radius:4px; font-weight:bold;">🍀 ${u}</span>`).join('');
                }
                
                const rankEl = document.getElementById('attRankingList');
                const todayEl = document.getElementById('attTodayList');
                if (rankEl) rankEl.innerHTML = rankHtml;
                if (todayEl) todayEl.innerHTML = todayHtml;
            }

            function stampAttendance(y, m, d) {
                const monthStr = `${y}-${String(m).padStart(2, '0')}`;
                const myName = window.myNickname || "익명";
                
                if (window.attendanceData && window.attendanceData[monthStr] && window.attendanceData[monthStr][myName] && window.attendanceData[monthStr][myName].includes(d)) {
                    alert("누나(작가님)! 오늘은 이미 클로버 도장을 꾹 찍었어!");
                    return;
                }

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "attendance", month: monthStr, day: d, nickname: myName }));
                }
            }

            function loadLocalBackground() {
                const bgType = localStorage.getItem('myBgType');
                const bgData = localStorage.getItem('myBgData');
                if (bgType === 'image' && bgData) {
                    document.getElementById('bgMediaWrapper').innerHTML = `<img src="${bgData}" alt="Full Background">`;
                } else if (bgType === 'youtube' && bgData) {
                    document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${bgData}?autoplay=1&mute=1&loop=1&playlist=${bgData}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
                }
            }

            function makeLinksClickable(text) {
                const urlRegex = /(https?:\/\/[^\s]+)/g;
                return text.replace(urlRegex, '<a href="$1" target="_blank" style="color: #ffeaa7; text-decoration: underline; padding: 0 4px;" onclick="event.stopPropagation()">$1</a>');
            }

            function formatNotice(text) {
                if (!text) return "";
                let formatted = makeLinksClickable(text);
                return formatted.replace(/\n/g, '<br>');
            }

            function addNotice() {
                const newVal = prompt("새로 추가할 공지를 적어주세요!\n(새 공지는 맨 위로 올라갑니다)");
                if (newVal !== null && newVal.trim() !== "") {
                    const combined = window.rawNotice ? ("📌 " + newVal + "\n\n" + window.rawNotice) : ("📌 " + newVal);
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "update_notice", notice: combined }));
                        window.rawNotice = combined;
                        document.getElementById('noticeText').innerHTML = formatNotice(combined);
                    }
                }
            }

            function editNotice() {
                const newVal = prompt("기존 공지를 전부 지우고 새로 쓰거나, 직접 글을 수정하세요!", window.rawNotice);
                if (newVal !== null) {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "update_notice", notice: newVal }));
                        window.rawNotice = newVal;
                        document.getElementById('noticeText').innerHTML = formatNotice(newVal);
                    }
                }
            }

            function toggleEmptySlots() {
                window.isHideEmpty = !window.isHideEmpty;
                applyEmptySlotVisibility();
            }

            function applyEmptySlotVisibility() {
                cardData.forEach((card, index) => {
                    const cardEl = document.getElementById(`card-card-${index}`);
                    if (cardEl) {
                        if (window.isHideEmpty && card.user.startsWith("자리")) {
                            cardEl.style.display = "none";
                        } else {
                            cardEl.style.display = "flex";
                        }
                    }
                });
            }

            function addMySlot() {
                const myName = window.myNickname || "익명";
                let emptyIdx = -1;
                for (let i = 0; i < cardData.length; i++) {
                    if (cardData[i].user.startsWith("자리")) {
                        emptyIdx = i;
                        break;
                    }
                }
                if (emptyIdx !== -1) {
                    const inputEl = document.getElementById(`username-${emptyIdx}`);
                    if (inputEl) inputEl.value = myName;
                    updateUsername(emptyIdx, myName);
                } else {
                    alert("아앗! 방에 빈자리가 하나도 안 남았어 누나!");
                }
            }

            function checkLogin() {
                document.getElementById('loginOverlay').style.display = 'flex';
                const savedNick = localStorage.getItem('mySavedNickname');
                if (savedNick) {
                    document.getElementById('nickInput').value = savedNick;
                    document.getElementById('pwInput').focus();
                }
            }

            function login() {
                const inputPw = document.getElementById('pwInput').value;
                const inputNick = document.getElementById('nickInput').value.trim();
                
                if (!inputNick) {
                    alert("누군지 알 수 있게 닉네임을 적어줘 누나!");
                    return;
                }

                if (inputNick === ADMIN_NICKNAME) {
                    if (inputPw !== ADMIN_PASSWORD) {
                        alert("앗! 이 닉네임은 방장(누나) 전용이야! 비밀번호가 틀렸어!");
                        return;
                    }
                    window.isAdmin = true;
                    document.getElementById('adminLogBtn').style.display = 'block';
                } else {
                    if (inputPw !== ROOM_PASSWORD) {
                        alert("비밀번호가 틀렸어! 다시 확인해봐.");
                        return;
                    }
                    window.isAdmin = false;
                }

                window.myNickname = inputNick; 
                localStorage.setItem('mySavedNickname', inputNick);
                
                document.getElementById('loginOverlay').style.display = 'none';
                initCards();
                connectWebSocket();
                loadLocalBackground(); 
            }

            function kickUser(nickname) {
                if(confirm(`${nickname} 님을 방에서 강제로 쫓아낼까?`)) {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "kick", target_nick: nickname }));
                    }
                }
            }

            let ws = null;
            let pingInterval = null; 
            
            const cardData = Array.from({length: 16}, (_, i) => ({ id: i+1, user: `자리${i+1}`, card_bg: null, is_mosaic: false, is_large: false, status: 0, work_start_time: 0 }));
            const myStreams = {}; 
            const peerConnections = {}; 
            const candidateBuffers = {}; 
            
            const expectedShares = {}; 
            const myOwnedSlots = new Set(); 

            const rtcConfig = {
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:stun1.l.google.com:19302' }
                ]
            };

            function getEmptySlotHTML(username) {
                if (!username || username.startsWith("자리")) {
                    return `<div style="position:relative; z-index:2; width:100%; text-align:center;"><span style="font-size:11px; color:#aaa;">화면 미공유 중</span></div>`;
                } else {
                    return `
                    <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; z-index:2; text-align:center; padding:10px; width:100%; height:100%;">
                        <span style="font-size:22px; font-weight:900; color:#fff; text-shadow: 2px 2px 5px rgba(0,0,0,0.9); margin-bottom:4px;">${username}</span>
                        <span style="font-size:11px; color:#aaa;">화면 미공유 중</span>
                    </div>`;
                }
            }

            function renderBox(index) {
                const box = document.getElementById(`stream-box-${index}`);
                if (!box) return;

                const existingVideo = box.querySelector('video');
                if (existingVideo) existingVideo.remove();

                const card = cardData[index];
                
                if (card.status > 0) {
                    let textMsg = "";
                    if (card.status === 1) textMsg = "🍽️ 식사중";
                    else if (card.status === 2) textMsg = "☕ 휴식중";
                    else if (card.status === 3) textMsg = "💤 수면중";

                    box.innerHTML = `
                        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; height:100%; background: rgba(0,0,0,0.7); z-index: 5; position: absolute; top:0; left:0;">
                            <div style="font-size: 28px; font-weight: 900; color: #fff; text-shadow: 2px 2px 6px rgba(0,0,0,0.8);">${textMsg}</div>
                        </div>
                    `;
                } else {
                    box.innerHTML = getEmptySlotHTML(card.user);
                }
            }

            function checkWorkTimeStart(index) {
                if (!cardData[index].work_start_time) {
                    const t = Date.now();
                    cardData[index].work_start_time = t;
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "set_work_time", index: index, time: t }));
                    }
                }
            }

            function checkWorkTimeStop(index) {
                const hasStream = !!myStreams[index];
                const hasStatus = cardData[index].status > 0;
                if (!hasStream && !hasStatus) {
                    cardData[index].work_start_time = 0;
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "set_work_time", index: index, time: 0 }));
                    }
                }
            }

            function resetWorkTimer(index) {
                const isMine = ((cardData[index].user === window.myNickname) && window.myNickname) || window.isAdmin;
                if (!isMine) {
                    alert("본인 카드나 관리자만 초기화할 수 있어!");
                    return;
                }
                cardData[index].work_start_time = Date.now();
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "set_work_time", index: index, time: cardData[index].work_start_time }));
                }
            }

            function updateStatusUI(index, status) {
                const btn = document.getElementById(`share-btn-status-${index}`);
                if (btn) {
                    if (status > 0) {
                        btn.innerText = "끄기";
                        btn.style.background = "#d63031"; 
                    } else {
                        btn.innerText = "상태";
                        btn.style.background = "#8e44ad"; 
                    }
                }
            }

            function handleStatusMainClick(index) {
                const isMine = ((cardData[index].user === window.myNickname) && window.myNickname) || window.isAdmin;
                if (!isMine) {
                    alert("자기 자리 상태만 바꿀 수 있어 누나!");
                    return;
                }

                if (cardData[index].status > 0) {
                    setStatus(index, 0);
                } else {
                    toggleStatusMenu(index);
                }
            }

            function toggleStatusMenu(index) {
                for(let i=0; i<16; i++) {
                    if (i !== index) {
                        const m = document.getElementById(`status-menu-${i}`);
                        if (m) m.style.display = 'none';
                    }
                }

                const menu = document.getElementById(`status-menu-${index}`);
                if (menu) {
                    menu.style.display = (menu.style.display === 'flex') ? 'none' : 'flex';
                }
            }

            function setStatus(index, s) {
                const menu = document.getElementById(`status-menu-${index}`);
                if(menu) menu.style.display = 'none';

                cardData[index].status = s;
                updateStatusUI(index, s); 

                if (s !== 0 && myStreams[index]) {
                    stopShare(index);
                }

                if (s !== 0) {
                    checkWorkTimeStart(index);
                } else {
                    checkWorkTimeStop(index);
                }

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "status_update", index: index, status: s }));
                }
                renderBox(index);
            }

            document.addEventListener('click', function(event) {
                if (!event.target.closest('.status-wrap')) {
                    for(let i=0; i<16; i++) {
                        const m = document.getElementById(`status-menu-${i}`);
                        if(m) m.style.display = 'none';
                    }
                }
            });

            setInterval(() => {
                const now = Date.now();
                cardData.forEach((card, idx) => {
                    const wtEl = document.getElementById(`work-timer-${idx}`);
                    if (wtEl && card.work_start_time) {
                        let s = Math.floor((now - card.work_start_time) / 1000);
                        let h = Math.floor(s / 3600); s %= 3600;
                        let m = Math.floor(s / 60); s %= 60;
                        wtEl.innerText = `⏱ ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
                        wtEl.style.display = 'inline-block';
                    } else if (wtEl) {
                        wtEl.style.display = 'none';
                    }
                });
            }, 1000);

            function logChat(sender, msg, timeStr) {
                const history = document.getElementById('chatHistory');
                const tSpan = timeStr ? `<span style="font-size:10px; color:#636e72; margin-left:6px;">${timeStr}</span>` : '';
                history.innerHTML += `<div style="margin-bottom: 5px;"><b>${sender}</b>: ${msg}${tSpan}</div>`;
                history.scrollTop = history.scrollHeight;
            }

            function clearChat() {
                if (confirm("채팅창을 전부 깨끗하게 지울까?")) {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "clear_chat" }));
                    }
                }
            }

            function forceRecoverWebRTC() {
                if (ws && ws.readyState === WebSocket.OPEN) {
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

            function initCards() {
                const grid = document.getElementById('cardGrid');
                grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    let bgStyle = card.card_bg ? `background-image: url('${card.card_bg}');` : '';
                    let mosaicBtnBg = card.is_mosaic ? '#e17055' : '#636e72';
                    let mosaicBtnText = card.is_mosaic ? '해제' : '모자이크';
                    
                    let sizeBtnText = card.is_large ? '작게' : '크게';
                    let sizeBtnBg = card.is_large ? '#e67e22' : '#f39c12';
                    let largeClass = card.is_large ? ' card-large' : '';
                    
                    let statusBtnText = card.status > 0 ? '끄기' : '상태';
                    let statusBtnBg = card.status > 0 ? '#d63031' : '#8e44ad';

                    grid.innerHTML += `
                        <div class="timer-card${largeClass}" id="card-card-${index}" style="${bgStyle}">
                            <div class="card-header">
                                <div style="display: flex; gap: 4px; align-items: center; width: 100%;">
                                    <input type="text" id="username-${index}" value="${card.user}" style="flex-grow: 1; min-width: 0; padding: 3px; font-size: 11px; font-weight: bold; text-align: center; background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.4); color: white; border-radius: 3px;" oninput="updateUsername(${index}, this.value)">
                                    <span id="work-timer-${index}" style="font-size: 10px; color: #ffeaa7; font-weight: bold; white-space: nowrap; display: ${card.work_start_time ? 'inline-block' : 'none'};">⏱ 00:00:00</span>
                                    <button onclick="resetWorkTimer(${index})" style="background:#d63031; color:white; border:none; border-radius:3px; padding:1px 4px; font-size:9px; cursor:pointer;" title="초기화">🔄</button>
                                </div>
                                
                                <div class="btn-group">
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
                                </div>
                            </div>
                            
                            <div style="display:flex; justify-content:space-between; align-items:center; position:relative; z-index:10; margin-top:2px;">
                                <input type="file" id="card-file-${index}" accept="image/jpeg, image/png, image/webp" style="font-size:9px; width:100%; color:#ccc;" onchange="setCardBackground(${index}, event)">
                            </div>

                            <div class="card-stream-box" id="stream-box-${index}"></div>
                        </div>
                    `;
                });
                
                cardData.forEach((_, i) => renderBox(i));
                applyEmptySlotVisibility();
            }

            function toggleSize(index) {
                const newState = !cardData[index].is_large;
                cardData[index].is_large = newState;
                applySizeUI(index, newState);
            }

            function applySizeUI(index, isLarge) {
                const cardEl = document.getElementById(`card-card-${index}`);
                const btn = document.getElementById(`size-btn-${index}`);
                
                if (cardEl) {
                    if (isLarge) cardEl.classList.add('card-large');
                    else cardEl.classList.remove('card-large');
                }
                if (btn) {
                    btn.innerText = isLarge ? "작게" : "크게";
                    btn.style.background = isLarge ? "#e67e22" : "#f39c12";
                }
            }

            function handleMosaicClick(index) {
                const isMyStream = !!myStreams[index];
                
                if (!isMyStream && !window.isAdmin) {
                    alert("본인이 화면 공유 중이 아니면 만질 수 없어!");
                    return;
                }

                const newState = !cardData[index].is_mosaic;
                cardData[index].is_mosaic = newState;
                applyMosaicUI(index, newState);

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "toggle_mosaic", index: index, is_mosaic: newState }));
                }
            }

            function toggleViewerSound(index) {
                const vid = document.getElementById(`remote-video-${index}`);
                const btn = document.getElementById(`sound-toggle-btn-${index}`);
                
                if (!vid) return;

                vid.muted = !vid.muted;
                if (vid.muted) {
                    btn.innerText = "소리켜기";
                    btn.style.background = "#b2bec3"; 
                } else {
                    btn.innerText = "음소거";
                    btn.style.background = "#00b894"; 
                }
            }

            function applyMosaicUI(index, isMosaic) {
                const btn = document.getElementById(`share-btn-mosaic-${index}`);
                if (btn) {
                    btn.innerText = isMosaic ? "해제" : "모자이크";
                    btn.style.background = isMosaic ? "#e17055" : "#636e72";
                }

                const remoteVideo = document.getElementById(`remote-video-${index}`);
                const localVideo = document.getElementById(`video-${index}`);
                
                const activeFilter = isMosaic ? 'blur(5px)' : 'none';

                if (remoteVideo) {
                    remoteVideo.style.filter = activeFilter;
                }
                if (localVideo) {
                    localVideo.style.filter = activeFilter;
                }
            }

            function updateUsername(index, val) {
                cardData[index].user = val;
                
                const myName = window.myNickname || "익명";
                if (val === myName) {
                    myOwnedSlots.add(index);
                } else {
                    myOwnedSlots.delete(index);
                }
                
                const box = document.getElementById(`stream-box-${index}`);
                if (box && !box.querySelector('video')) {
                    renderBox(index);
                }

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "username_change", index: index, user: val }));
                }
                
                applyEmptySlotVisibility();
            }

            function setCardBackground(index, event) {
                const file = event.target.files[0];
                if (!file) return;

                if (file.type === "image/gif") {
                    alert("데이터 폭발을 막기 위해 움짤(GIF)은 올릴 수 없어 누나!");
                    event.target.value = ""; 
                    return;
                }

                const reader = new FileReader();
                reader.onload = function(e) {
                    const dataUrl = e.target.result;
                    cardData[index].card_bg = dataUrl;
                    const cardEl = document.getElementById(`card-card-${index}`);
                    if (cardEl) { cardEl.style.backgroundImage = `url('${dataUrl}')`; }

                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "card_bg_change", index: index, dataUrl: dataUrl }));
                    }
                };
                reader.readAsDataURL(file);
            }

            function setLocalBackground(event) {
                const file = event.target.files[0];
                if (!file) return;
                
                if (file.type === "image/gif") {
                    alert("데이터 폭발을 막기 위해 움짤(GIF)은 올릴 수 없어 누나!");
                    event.target.value = ""; 
                    return;
                }

                const reader = new FileReader();
                reader.onload = function(e) {
                    const dataUrl = e.target.result;
                    document.getElementById('bgMediaWrapper').innerHTML = `<img src="${dataUrl}" alt="Full Background">`;
                    
                    localStorage.setItem('myBgType', 'image');
                    try {
                        localStorage.setItem('myBgData', dataUrl);
                    } catch (err) {
                        alert("사진 용량이 너무 커서 다음 접속 시엔 풀릴 수 있어! 가급적 작은 용량의 사진을 써줘!");
                    }
                };
                reader.readAsDataURL(file);
            }

            function setYoutubeBackground() {
                const inputVal = document.getElementById('bgYoutubeInput').value;
                const videoId = extractYoutubeId(inputVal);
                if (!videoId) { alert("유튜브 링크가 올바르지 않습니다."); return; }

                document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&loop=1&playlist=${videoId}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
                
                localStorage.setItem('myBgType', 'youtube');
                localStorage.setItem('myBgData', videoId);
            }

            async function toggleShare(index, type) {
                const box = document.getElementById(`stream-box-${index}`);
                const btnScreen = document.getElementById(`share-btn-screen-${index}`);
                const btnCam = document.getElementById(`share-btn-cam-${index}`);
                
                if (myStreams[index]) {
                    stopShare(index);
                    return;
                }

                if (cardData[index].status > 0) {
                    cardData[index].status = 0;
                    updateStatusUI(index, 0);
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "status_update", index: index, status: 0 }));
                    }
                }

                try {
                    let stream;
                    if (type === 'screen') {
                        stream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: "always", frameRate: 15 }, audio: true });
                        btnScreen.innerText = "중지";
                        btnScreen.style.background = "#d63031";
                        btnCam.style.display = "none"; 
                    } else {
                        stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
                        btnCam.innerText = "중지";
                        btnCam.style.background = "#d63031";
                        btnScreen.style.display = "none"; 
                    }
                    
                    myStreams[index] = stream;

                    const myName = window.myNickname || "익명";
                    const userEl = document.getElementById(`username-${index}`);
                    if (userEl) userEl.value = myName;
                    updateUsername(index, myName);

                    let filterStyle = cardData[index].is_mosaic ? `filter: blur(5px);` : '';
                    
                    box.innerHTML = `<video id="video-${index}" autoplay playsinline muted disablePictureInPicture style="${filterStyle}"></video>`;
                    const localVideo = document.getElementById(`video-${index}`);
                    localVideo.srcObject = stream;
                    localVideo.play().catch(e => console.log(e));

                    checkWorkTimeStart(index);

                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "start_share", index: index }));
                    }

                    stream.getVideoTracks()[0].onended = () => { stopShare(index); };
                } catch (err) {
                    console.error("미디어 캡처 에러:", err);
                }
            }

            function stopShare(index) {
                if (myStreams[index]) {
                    myStreams[index].getTracks().forEach(track => track.stop());
                    delete myStreams[index];
                }

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "stop_share", index: index }));
                }

                checkWorkTimeStop(index);

                renderBox(index);
                
                const btnScreen = document.getElementById(`share-btn-screen-${index}`);
                const btnCam = document.getElementById(`share-btn-cam-${index}`);
                if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; }
                if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; }
                
                const soundBtn = document.getElementById(`sound-toggle-btn-${index}`);
                if (soundBtn) { soundBtn.style.display = "none"; }
            }

            setInterval(() => {
                if (!ws || ws.readyState !== WebSocket.OPEN || !ws.clientId) return;
                
                for (let idx in expectedShares) {
                    const sharerId = expectedShares[idx];
                    if (sharerId === ws.clientId) continue; 
                    
                    const pcKey = `${idx}_${sharerId}`;
                    const pc = peerConnections[pcKey];
                    
                    let isConnectionDead = false;
                    if (!pc) {
                        isConnectionDead = true;
                    } else {
                        const state = pc.iceConnectionState;
                        if (state === 'disconnected' || state === 'failed' || state === 'closed') {
                            isConnectionDead = true;
                        }
                    }

                    if (isConnectionDead) {
                        if (pc) {
                            try { pc.close(); } catch(e) {}
                        }
                        delete peerConnections[pcKey];
                        ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: sharerId }));
                    }
                }
            }, 5000);

            function extractYoutubeId(url) {
                if (!url) return null;
                url = url.trim();
                if (url.length === 11) return url;
                const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/;
                const match = url.match(regExp);
                return (match && match[2].length === 11) ? match[2] : null;
            }

            function connectWebSocket() {
                const loc = window.location;
                let wsProtocol = loc.protocol === "https:" ? "wss://" : "ws://";
                const wsUrl = wsProtocol + loc.host + "/ws";

                try {
                    ws = new WebSocket(wsUrl);

                    ws.onopen = function() {
                        const statusEl = document.getElementById('connStatus');
                        statusEl.innerText = "연결됨";
                        statusEl.className = "status-indicator status-online";

                        const myNick = window.myNickname || "익명";
                        const ownedArr = Array.from(myOwnedSlots);
                        ws.send(JSON.stringify({ type: "set_nickname", nickname: myNick, owned: ownedArr }));

                        if (pingInterval) clearInterval(pingInterval);
                        pingInterval = setInterval(() => {
                            if (ws && ws.readyState === WebSocket.OPEN) {
                                ws.send(JSON.stringify({ type: "ping" }));
                            }
                        }, 20000); 
                    };

                    ws.onmessage = async function(event) {
                        try {
                            const data = JSON.parse(event.data);
                            
                            if (data.type === "pong") {
                                return;
                            }
                            else if (data.type === "kicked") {
                                alert("방장에 의해 방에서 쫓겨났어!");
                                localStorage.removeItem('mySavedNickname'); 
                                window.location.reload(); 
                            }
                            else if (data.type === "chat_cleared") {
                                document.getElementById('chatHistory').innerHTML = "";
                            }
                            
                            else if (data.type === "user_list") {
                                document.getElementById('userCount').innerText = data.count + "명";
                                
                                let listHtml = data.users.map(u => {
                                    let kickBtn = '';
                                    if (window.isAdmin && u.nickname !== window.myNickname) {
                                        kickBtn = `<button onclick="kickUser('${u.nickname}')" style="background:#d63031; border:none; color:white; border-radius:3px; padding:1px 4px; font-size:9px; cursor:pointer; margin-left:4px;">강퇴</button>`;
                                    }
                                    return `<span style="background:rgba(255,255,255,0.1); padding:3px 8px; border-radius:4px; display:inline-flex; align-items:center;">
                                        <b style="color:white;">${u.nickname}</b>${kickBtn}
                                    </span>`;
                                }).join("");
                                document.getElementById('userListStr').innerHTML = listHtml;
                            }
                            else if (data.type === "chat") {
                                logChat(data.senderName, data.msg, data.time);
                            } 
                            else if (data.type === "update_notice") {
                                window.rawNotice = data.notice;
                                document.getElementById('noticeText').innerHTML = formatNotice(data.notice);
                            }
                            else if (data.type === "status_update") {
                                cardData[data.index].status = data.status;
                                updateStatusUI(data.index, data.status);
                                const box = document.getElementById(`stream-box-${data.index}`);
                                if (box && !box.querySelector('video')) {
                                    renderBox(data.index);
                                }
                            }
                            else if (data.type === "set_work_time") {
                                cardData[data.index].work_start_time = data.time;
                                const wtEl = document.getElementById(`work-timer-${data.index}`);
                                if (wtEl) {
                                    if (data.time) {
                                        wtEl.style.display = 'inline-block';
                                    } else {
                                        wtEl.style.display = 'none';
                                    }
                                }
                            }
                            else if (data.type === "init_state") {
                                const state = data.state;
                                
                                if (state.global_notice) {
                                    window.rawNotice = state.global_notice;
                                    document.getElementById('noticeText').innerHTML = formatNotice(state.global_notice);
                                }
                                
                                if (state.attendance) {
                                    window.attendanceData = state.attendance;
                                }

                                if (state.admin_log) {
                                    window.adminLogData = state.admin_log;
                                    if (window.isAdmin && document.getElementById('adminLogModal').style.display === 'flex') {
                                        renderAdminLog();
                                    }
                                }

                                if (state.cards) {
                                    state.cards.forEach((card, i) => {
                                        if (cardData[i]) {
                                            cardData[i].user = card.user;
                                            cardData[i].card_bg = card.card_bg;
                                            cardData[i].is_mosaic = card.is_mosaic || false;
                                            cardData[i].is_large = card.is_large || false;
                                            cardData[i].status = card.status || 0;
                                            cardData[i].work_start_time = card.work_start_time || 0;
                                            
                                            applyMosaicUI(i, cardData[i].is_mosaic);
                                            applySizeUI(i, cardData[i].is_large);
                                            updateStatusUI(i, cardData[i].status);

                                            const userEl = document.getElementById(`username-${i}`);
                                            if (userEl) userEl.value = card.user;
                                            const cardEl = document.getElementById(`card-card-${i}`);
                                            if (cardEl && card.card_bg) { cardEl.style.backgroundImage = `url('${card.card_bg}')`; }
                                            
                                            const wtEl = document.getElementById(`work-timer-${i}`);
                                            if (wtEl) {
                                                if (card.work_start_time) {
                                                    wtEl.style.display = 'inline-block';
                                                } else {
                                                    wtEl.style.display = 'none';
                                                }
                                            }
                                            
                                            const box = document.getElementById(`stream-box-${i}`);
                                            if (box && !box.querySelector('video')) {
                                                renderBox(i);
                                            }
                                        }
                                    });
                                }

                                if (state.chat_history) {
                                    const historyEl = document.getElementById('chatHistory');
                                    historyEl.innerHTML = "";
                                    state.chat_history.forEach(chat => {
                                        const tSpan = chat.time ? `<span style="font-size:10px; color:#636e72; margin-left:6px;">${chat.time}</span>` : '';
                                        historyEl.innerHTML += `<div style="margin-bottom: 5px;"><b>${chat.senderName}</b>: ${chat.msg}${tSpan}</div>`;
                                    });
                                    historyEl.scrollTop = historyEl.scrollHeight;
                                }
                                
                                applyEmptySlotVisibility();
                            }
                            else if (data.type === "attendance_update") {
                                window.attendanceData = data.attendance;
                                if (document.getElementById('attendanceModal').style.display === 'flex') {
                                    renderAttendanceBoard();
                                }
                            }
                            else if (data.type === "admin_log_update") {
                                if (!window.adminLogData) window.adminLogData = [];
                                window.adminLogData.push(data.log);
                                if (window.adminLogData.length > 100) window.adminLogData.shift();
                                if (window.isAdmin && document.getElementById('adminLogModal').style.display === 'flex') {
                                    renderAdminLog();
                                }
                            }
                            else if (data.type === "username_change") {
                                cardData[data.index].user = data.user;
                                const inputEl = document.getElementById(`username-${data.index}`);
                                if (inputEl) { inputEl.value = data.user; }
                                
                                const myName = window.myNickname || "익명";
                                if (data.user === myName) {
                                    myOwnedSlots.add(data.index);
                                } else {
                                    myOwnedSlots.delete(data.index);
                                }
                                
                                const box = document.getElementById(`stream-box-${data.index}`);
                                if (box && !box.querySelector('video')) {
                                    renderBox(data.index);
                                }
                                
                                applyEmptySlotVisibility();
                                
                            } else if (data.type === "card_bg_change") {
                                cardData[data.index].card_bg = data.dataUrl;
                                const cardEl = document.getElementById(`card-card-${data.index}`);
                                if (cardEl) { cardEl.style.backgroundImage = `url('${data.dataUrl}')`; }
                            } 
                            else if (data.type === "toggle_mosaic") {
                                if (cardData[data.index]) {
                                    cardData[data.index].is_mosaic = data.is_mosaic;
                                    applyMosaicUI(data.index, data.is_mosaic);
                                }
                            }
                            else if (data.type === "start_share") {
                                const targetIndex = data.index;
                                const sharerId = data.sender;

                                if (data.target && data.target !== ws.clientId) return;

                                expectedShares[targetIndex] = sharerId;

                                if (ws.clientId && sharerId !== ws.clientId && ws.readyState === WebSocket.OPEN) {
                                    ws.send(JSON.stringify({ type: "request_offer", index: targetIndex, target: sharerId }));
                                }
                            }
                            else if (data.type === "request_offer" && data.target === ws.clientId) {
                                const targetIndex = data.index;
                                const viewerId = data.sender;

                                if (myStreams[targetIndex]) {
                                    await createOfferForViewer(targetIndex, viewerId);
                                }
                            }
                            else if (data.type === "offer" && data.target === ws.clientId) {
                                const index = data.index;
                                const senderId = data.sender;
                                const pcKey = `${index}_${senderId}`;

                                if (peerConnections[pcKey]) {
                                    try { peerConnections[pcKey].close(); } catch(e) {}
                                }

                                const pc = new RTCPeerConnection(rtcConfig);
                                peerConnections[pcKey] = pc;

                                pc.oniceconnectionstatechange = () => {
                                    const state = pc.iceConnectionState;
                                    if (state === 'disconnected' || state === 'failed' || state === 'closed') {
                                        try { pc.close(); } catch(e) {}
                                        delete peerConnections[pcKey];
                                    }
                                };

                                pc.ontrack = (e) => {
                                    const box = document.getElementById(`stream-box-${index}`);
                                    
                                    let filterStyle = cardData[index].is_mosaic ? `filter: blur(5px);` : '';
                                    
                                    box.innerHTML = `<video id="remote-video-${index}" autoplay playsinline disablePictureInPicture style="${filterStyle}"></video>`;
                                    const remoteVideo = document.getElementById(`remote-video-${index}`);
                                    remoteVideo.srcObject = e.streams[0];
                                    remoteVideo.play().catch(err => console.log(err));
                                    
                                    const soundBtn = document.getElementById(`sound-toggle-btn-${index}`);
                                    if (soundBtn) {
                                        soundBtn.style.display = "inline-block";
                                        soundBtn.innerText = "소리끄기";
                                        soundBtn.style.background = "#00b894"; 
                                    }
                                };

                                pc.onicecandidate = (e) => {
                                    if (e.candidate && ws && ws.readyState === WebSocket.OPEN) {
                                        ws.send(JSON.stringify({ type: "ice", index: index, target: senderId, candidate: e.candidate }));
                                    }
                                };

                                await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));

                                if (candidateBuffers[pcKey]) {
                                    for (const cand of candidateBuffers[pcKey]) {
                                        await pc.addIceCandidate(new RTCIceCandidate(cand)).catch(e => console.log(e));
                                    }
                                    delete candidateBuffers[pcKey];
                                }

                                const answer = await pc.createAnswer();
                                await pc.setLocalDescription(answer);

                                if (ws && ws.readyState === WebSocket.OPEN) {
                                    ws.send(JSON.stringify({ type: "answer", index: index, target: senderId, sdp: pc.localDescription }));
                                }
                            } 
                            else if (data.type === "answer" && data.target === ws.clientId) {
                                const pcKey = `${data.index}_${data.sender}`;
                                const pc = peerConnections[pcKey];
                                if (pc) await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
                            } 
                            else if (data.type === "ice" && data.target === ws.clientId) {
                                const pcKey = `${data.index}_${data.sender}`;
                                const pc = peerConnections[pcKey];
                                
                                if (pc && pc.remoteDescription && pc.remoteDescription.type) {
                                    await pc.addIceCandidate(new RTCIceCandidate(data.candidate)).catch(e => console.log(e));
                                } else {
                                    if (!candidateBuffers[pcKey]) candidateBuffers[pcKey] = [];
                                    candidateBuffers[pcKey].push(data.candidate);
                                }
                            } 
                            else if (data.type === "stop_share") {
                                const index = data.index;
                                delete expectedShares[index]; 

                                for (let key in peerConnections) {
                                    if (key.startsWith(`${index}_`)) {
                                        try {
                                            peerConnections[key].getSenders().forEach(sender => peerConnections[key].removeTrack(sender));
                                            peerConnections[key].close();
                                        } catch(e) {}
                                        delete peerConnections[key];
                                    }
                                }
                                
                                renderBox(index);
                                
                                const btnScreen = document.getElementById(`share-btn-screen-${index}`);
                                const btnCam = document.getElementById(`share-btn-cam-${index}`);
                                if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; }
                                if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; }
                                
                                const soundBtn = document.getElementById(`sound-toggle-btn-${index}`);
                                if (soundBtn) { soundBtn.style.display = "none"; }
                            }
                            else if (data.type === "welcome") {
                                ws.clientId = data.clientId;
                                
                                setTimeout(() => {
                                    if (ws && ws.readyState === WebSocket.OPEN) {
                                        ws.send(JSON.stringify({ type: "request_existing_shares" }));
                                        
                                        for (let idx in myStreams) {
                                            ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx) }));
                                        }
                                    }
                                }, 800);
                            }
                            else if (data.type === "request_existing_shares") {
                                for (let idx in myStreams) {
                                    if (ws && ws.readyState === WebSocket.OPEN) {
                                        ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx), target: data.sender }));
                                    }
                                }
                            }
                        } catch(e) {
                            console.error("데이터 처리 에러:", e);
                        }
                    };

                    ws.onclose = function() {
                        if (pingInterval) clearInterval(pingInterval);
                        const statusEl = document.getElementById('connStatus');
                        if (statusEl) {
                            statusEl.innerText = "서버 업데이트 중...";
                            statusEl.className = "status-indicator status-offline";
                        }
                        
                        setTimeout(() => {
                            window.location.reload();
                        }, 3000);
                    };
                } catch(e) {
                    setTimeout(connectWebSocket, 2000);
                }
            }

            async function createOfferForViewer(index, viewerId) {
                const pcKey = `${index}_${viewerId}`;
                if (peerConnections[pcKey]) {
                    try { peerConnections[pcKey].close(); } catch(e) {}
                }

                const pc = new RTCPeerConnection(rtcConfig);
                peerConnections[pcKey] = pc;

                pc.oniceconnectionstatechange = () => {
                    const state = pc.iceConnectionState;
                    if (state === 'disconnected' || state === 'failed' || state === 'closed') {
                        try {
                            pc.getSenders().forEach(sender => pc.removeTrack(sender));
                            pc.close();
                        } catch(e) {}
                        delete peerConnections[pcKey];
                    }
                };

                const stream = myStreams[index];
                if (stream) {
                    stream.getTracks().forEach(track => pc.addTrack(track, stream));
                }

                pc.onicecandidate = (e) => {
                    if (e.candidate && ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "ice", index: index, target: viewerId, candidate: e.candidate }));
                    }
                };

                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "offer", index: index, target: viewerId, sdp: pc.localDescription }));
                }
            }

            function sendChat() {
                const input = document.getElementById('chatInput');
                const msgText = input.value.trim();
                if (!msgText) return;

                const myName = window.myNickname || "익명";
                
                const now = new Date();
                const month = now.getMonth() + 1;
                const date = now.getDate();
                const timeString = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
                const timeStr = `${month}/${date} ${timeString}`;

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "chat", senderName: myName, msg: msgText, time: timeStr }));
                    input.value = '';
                }
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
    
    await websocket.send_text(json.dumps({"type": "welcome", "clientId": client_id}))
    await websocket.send_text(json.dumps({"type": "init_state", "state": server_state}))
    
    try:
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
                manager.active_users[websocket] = nickname
                await manager.broadcast_user_list()
                
                if client_id not in manager.active_slots:
                    manager.active_slots[client_id] = []

                log_entry = {"msg": f"{nickname} 님이 입장했습니다.", "time": __import__('time').time()}
                server_state.setdefault("admin_log", []).append(log_entry)
                if len(server_state["admin_log"]) > 100:
                    server_state["admin_log"].pop(0)
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
                                
                                change_packet = json.dumps({
                                    "type": "username_change",
                                    "index": idx,
                                    "user": nickname
                                })
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
                    
                    change_packet = json.dumps({
                        "type": "username_change",
                        "index": assigned_idx,
                        "user": nickname
                    })
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
                        try:
                            await ws_conn.send_text(json.dumps({"type": "kicked"}))
                        except:
                            pass
                continue

            if p_type == "chat":
                chat_obj = {
                    "senderName": packet.get("senderName"), 
                    "msg": packet.get("msg"),
                    "time": packet.get("time", "")
                }
                server_state["chat_history"].append(chat_obj)
                if len(server_state["chat_history"]) > 100:
                    server_state["chat_history"].pop(0)
                
                await manager.broadcast(json.dumps(packet))
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                
            elif p_type == "attendance":
                month = packet.get("month")
                day = packet.get("day")
                nickname = packet.get("nickname")
                
                if "attendance" not in server_state:
                    server_state["attendance"] = {}
                if month not in server_state["attendance"]:
                    server_state["attendance"][month] = {}
                if nickname not in server_state["attendance"][month]:
                    server_state["attendance"][month][nickname] = []
                    
                if day not in server_state["attendance"][month][nickname]:
                    server_state["attendance"][month][nickname].append(day)
                    
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                await manager.broadcast(json.dumps({"type": "attendance_update", "attendance": server_state["attendance"]}))
                
            else:
                packet["sender"] = client_id
                
                if p_type == "username_change":
                    idx = packet["index"]
                    val = packet["user"]
                    server_state["cards"][idx]["user"] = val
                    
                    if not val.startswith("자리"):
                        if client_id not in manager.active_slots:
                            manager.active_slots[client_id] = []
                        if idx not in manager.active_slots[client_id]:
                            manager.active_slots[client_id].append(idx)
                            
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
                    if idx in manager.active_shares:
                        del manager.active_shares[idx]
                elif p_type == "update_notice":
                    server_state["global_notice"] = packet.get("notice", "")
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                elif p_type == "status_update":
                    server_state["cards"][packet["index"]]["status"] = packet.get("status", 0)
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                elif p_type == "set_work_time":
                    server_state["cards"][packet["index"]]["work_start_time"] = packet.get("time", 0)
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                
                await manager.broadcast(json.dumps(packet), exclude=websocket)

    except (WebSocketDisconnect, Exception):
        client_id = str(id(websocket))
        
        nickname = manager.active_users.get(websocket, "")
        
        reverted_indexes = []
        if client_id in manager.active_slots:
            for r_idx in manager.active_slots[client_id]:
                is_claimed_by_other = False
                for other_cid, slots in manager.active_slots.items():
                    if other_cid != client_id and r_idx in slots:
                        is_claimed_by_other = True
                        break
                
                if not is_claimed_by_other:
                    server_state["cards"][r_idx]["user"] = f"자리{r_idx+1}"
                    server_state["cards"][r_idx]["work_start_time"] = 0
                    server_state["cards"][r_idx]["is_large"] = False
                    server_state["cards"][r_idx]["status"] = 0
                    reverted_indexes.append(r_idx)
                    
            del manager.active_slots[client_id]
            asyncio.create_task(asyncio.to_thread(save_data, server_state))

        freed_indexes = manager.disconnect(websocket)
        await manager.broadcast_user_list()
        
        if nickname and nickname != "연결중...":
            log_entry = {"msg": f"{nickname} 님이 퇴장했습니다.", "time": __import__('time').time()}
            server_state.setdefault("admin_log", []).append(log_entry)
            if len(server_state["admin_log"]) > 100:
                server_state["admin_log"].pop(0)
            asyncio.create_task(asyncio.to_thread(save_data, server_state))
            await manager.broadcast(json.dumps({"type": "admin_log_update", "log": log_entry}))
        
        for r_idx in reverted_indexes:
            await manager.broadcast(json.dumps({
                "type": "username_change",
                "index": r_idx,
                "user": f"자리{r_idx+1}"
            }))
            
        for idx in freed_indexes:
            await manager.broadcast(json.dumps({"type": "stop_share", "index": idx}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
