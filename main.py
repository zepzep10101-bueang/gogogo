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
                new_cards = [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False} for i in range(len(cards), 16)]
                data["cards"].extend(new_cards)
            
            for i, card in enumerate(data["cards"]):
                card["user"] = f"자리{i+1}"
                card["is_mosaic"] = False
                if "stopwatch" not in card:
                    card["stopwatch"] = {"is_active": False, "is_running": False, "start_time": 0, "elapsed": 0}
                if "work_start_time" not in card:
                    card["work_start_time"] = 0
            
            if "global_notice" not in data:
                data["global_notice"] = "📌 다 함께 모여서 열심히 마감해 봅시다!"
                
            return data
    except Exception:
        pass
    
    initial_data = {
        "_id": "main_state",
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "stopwatch": {"is_active": False, "is_running": False, "start_time": 0, "elapsed": 0}, "work_start_time": 0} for i in range(16)],
        "global_bg": None,
        "global_bg_type": None,
        "chat_history": [],
        "global_notice": "📌 다 함께 모여서 열심히 마감해 봅시다!"
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
        users = [name for name in self.active_users.values() if name != "연결중..."]
        msg = json.dumps({"type": "user_list", "count": len(self.active_connections), "users": users})
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
            
            .card-grid { display: grid; gap: 15px; align-content: start; justify-content: center; width: 100%; min-width: 0; }
            .grid-1 { grid-template-columns: minmax(0, 900px); }
            .grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
            .grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .grid-5-6 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
            .grid-max { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
            
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 12px; padding: 8px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); backdrop-filter: blur(5px); min-width: 250px; min-height: 250px; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: box-shadow 0.3s ease; resize: both; }

            .card-header { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 3; width: 100%; cursor: move; }
            
            .btn-group { display: flex; gap: 2px; width: 100%; justify-content: center; flex-wrap: nowrap; }
            .share-btn { padding: 4px 2px; font-size: 10px; color: white; border: none; border-radius: 3px; cursor: pointer; white-space: nowrap; font-weight: bold; text-align: center; flex-grow: 1; }

            .card-stream-box { width: 100%; flex-grow: 1; min-height: 150px; background: rgba(0, 0, 0, 0.15); border-radius: 8px; overflow: hidden; position: relative; margin-top: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 2; pointer-events: none; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: transparent; position: absolute; top: 0; left: 0; z-index: 10; transition: filter 0.2s ease-in-out; pointer-events: auto; }

            .side-panel { display: flex; flex-direction: column; gap: 15px; position: sticky; top: 20px; height: calc(100vh - 40px); min-width: 0; }
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 12px; border: 1px solid rgba(255, 255, 255, 0.2); backdrop-filter: blur(5px); min-width: 0; word-break: keep-all; overflow-wrap: break-word; }
            
            .settings-toggle-btn { background: #636e72; color: white; border: none; border-radius: 4px; padding: 3px 6px; font-size: 11px; cursor: pointer; font-weight: normal; margin-left: 2px; white-space: nowrap; }
            .settings-dropdown { display: none; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.2); }

            .chat-box { display: flex; flex-direction: column; flex-grow: 1; min-height: 0; height: 100%; }
            #chatHistory { flex-grow: 1; overflow-y: auto; margin-top: 8px; font-size: 13px; color: #ddd; line-height: 1.5; word-break: break-all; padding-right: 4px; }
            .chat-input { display: flex; margin-top: 8px; gap: 4px; }
            .chat-input input { flex-grow: 1; padding: 7px; border-radius: 4px; border: none; background: rgba(255, 255, 255, 0.9); color: black; min-width: 0; font-size: 12px; }
            .chat-input button { padding: 7px 10px; background: #ff7675; border: none; color: white; border-radius: 4px; cursor: pointer; flex-shrink: 0; font-size: 12px; }
            
            .bg-control { display: flex; flex-direction: column; gap: 6px; margin-top: 5px; }
            .status-indicator { font-size: 11px; padding: 2px 6px; border-radius: 3px; display: inline-block; margin-left: 5px; }
            .status-online { background: #00b894; color: white; }
            .status-offline { background: #d63031; color: white; }
            
            .recovery-btn { background: #d63031; color: white; border: none; border-radius: 4px; padding: 3px 6px; font-size: 10px; cursor: pointer; font-weight: bold; white-space: nowrap; }
        </style>
    </head>
    <body>

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
                        <div style="display: flex; flex-direction: column; gap: 3px; align-items: flex-end; flex-grow: 1;">
                            <div style="display: flex; gap: 3px;">
                                <button class="settings-toggle-btn" style="background:#0984e3; font-weight:bold;" onclick="addMySlot()">➕ 자리</button>
                                <button class="settings-toggle-btn" onclick="toggleSettingsPanel()">⚙️ 배경</button>
                            </div>
                            <button class="settings-toggle-btn" style="background:#ff7675; width: 100%; text-align: center; font-weight: bold; margin-left: 0;" onclick="toggleNoticePanel()">📢 공지</button>
                        </div>
                    </div>

                    <span id="connStatus" class="status-indicator status-offline" style="margin-top:4px;">연결 중...</span>
                    <p style="margin-top:6px; font-size:13px;">인원: <span id="userCount" style="color:#ff7675; font-weight:bold;">0명</span></p>
                    
                    <p style="margin-top:4px; font-size:11px; color:#aaa; line-height:1.5;">명단:<br><span id="userListStr" style="display:flex; flex-wrap:wrap; gap:4px; margin-top:2px;"></span></p>

                    <div id="noticeDropdown" style="display: none; margin-top: 10px; padding: 8px; background: rgba(0,0,0,0.3); border-radius: 5px; border: 1px solid rgba(255, 118, 117, 0.4);">
                        <div style="text-align: right; margin-bottom: 6px;">
                            <button onclick="addNotice()" style="background:#0984e3; color:white; border:none; border-radius:3px; padding:2px 6px; font-size:9px; cursor:pointer; font-weight:bold; margin-right: 3px;">➕ 추가</button>
                            <button onclick="editNotice()" style="background:#ff7675; color:white; border:none; border-radius:3px; padding:2px 6px; font-size:9px; cursor:pointer; font-weight:bold;">✏️ 수정/삭제</button>
                        </div>
                        <div id="noticeText" style="font-size: 12px; color: #fff; line-height: 1.5; word-break: break-all;">공지사항 로딩 중...</div>
                    </div>
                    
                    <div class="settings-dropdown" id="settingsDropdown">
                        <div style="font-size: 11px; font-weight: bold; color: #fff; margin-bottom: 4px;">🖼️ 배경 꾸미기</div>
                        <div class="bg-control">
                            <div style="font-size: 10px; color: #aaa;">일반 사진 (움짤X):</div>
                            <input type="file" id="bgFileInput" accept="image/jpeg, image/png, image/webp" style="font-size:9px; width:100%;" onchange="setLocalBackground(event)">
                            
                            <div style="font-size: 10px; color: #aaa; margin-top: 3px;">유튜브 링크:</div>
                            <div style="display:flex; gap:3px;">
                                <input type="text" id="bgYoutubeInput" placeholder="URL" style="flex-grow:1; font-size:9px; padding:3px; background:rgba(255,255,255,0.9); color:black; border:none; border-radius:3px; min-width:0;">
                                <button onclick="setYoutubeBackground()" style="font-size:9px; padding:3px 5px; background:#ff7675; border:none; color:white; border-radius:3px; cursor:pointer;">적용</button>
                            </div>
                        </div>
                    </div>
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
            const ADMIN_NICKNAME = "부엉";

            window.rawNotice = ""; 

            function makeFreeDraggable(el) {
                let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
                const header = el.querySelector('.card-header');
                if (header) { header.onmousedown = dragMouseDown; } else { el.onmousedown = dragMouseDown; }

                function dragMouseDown(e) {
                    if (['input', 'button'].includes(e.target.tagName.toLowerCase())) return;
                    e.preventDefault();
                    pos3 = e.clientX; pos4 = e.clientY;
                    document.onmouseup = closeDragElement;
                    document.onmousemove = elementDrag;
                    if (el.style.position !== 'fixed') {
                        const rect = el.getBoundingClientRect();
                        el.style.position = 'fixed'; el.style.zIndex = '9999';
                        el.style.width = rect.width + 'px'; el.style.height = rect.height + 'px';
                        el.style.left = rect.left + 'px'; el.style.top = rect.top + 'px';
                        el.style.boxShadow = '0 10px 25px rgba(0,0,0,0.8)';
                    }
                }
                function elementDrag(e) {
                    e.preventDefault();
                    pos1 = pos3 - e.clientX; pos2 = pos4 - e.clientY;
                    pos3 = e.clientX; pos4 = e.clientY;
                    el.style.top = (el.offsetTop - pos2) + "px";
                    el.style.left = (el.offsetLeft - pos1) + "px";
                }
                function closeDragElement() { document.onmouseup = null; document.onmousemove = null; }
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

            function toggleNoticePanel() {
                const dropdown = document.getElementById('noticeDropdown');
                dropdown.style.display = (dropdown.style.display === 'block') ? 'none' : 'block';
            }

            function addNotice() {
                const newVal = prompt("새로 추가할 공지를 적어주세요!");
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
                const newVal = prompt("기존 공지를 전부 지우고 새로 쓰세요!", window.rawNotice);
                if (newVal !== null) {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "update_notice", notice: newVal }));
                        window.rawNotice = newVal;
                        document.getElementById('noticeText').innerHTML = formatNotice(newVal);
                    }
                }
            }

            function applyEmptySlotVisibility() {
                let visibleCount = 0;
                cardData.forEach((card, index) => {
                    const cardEl = document.getElementById(`card-card-${index}`);
                    if (cardEl) {
                        if (card.user.startsWith("자리")) { cardEl.style.display = "none"; } 
                        else { cardEl.style.display = "flex"; visibleCount++; }
                    }
                });
                const grid = document.getElementById('cardGrid');
                if (grid) {
                    grid.className = 'card-grid';
                    if (visibleCount === 1) grid.classList.add('grid-1');
                    else if (visibleCount === 2) grid.classList.add('grid-2');
                    else if (visibleCount === 3) grid.classList.add('grid-3');
                    else if (visibleCount === 4) grid.classList.add('grid-4');
                    else if (visibleCount <= 6) grid.classList.add('grid-5-6');
                    else grid.classList.add('grid-max');
                }
            }

            function addMySlot() {
                const myName = window.myNickname || "익명";
                let emptyIdx = -1;
                for (let i = 0; i < cardData.length; i++) { if (cardData[i].user.startsWith("자리")) { emptyIdx = i; break; } }
                if (emptyIdx !== -1) {
                    const inputEl = document.getElementById(`username-${emptyIdx}`);
                    if (inputEl) inputEl.value = myName;
                    updateUsername(emptyIdx, myName);
                } else { alert("아앗! 방에 빈자리가 하나도 안 남았어 누나!"); }
            }

            function toggleSettingsPanel() {
                const dropdown = document.getElementById('settingsDropdown');
                dropdown.style.display = (dropdown.style.display === 'block') ? 'none' : 'block';
            }

            function checkLogin() {
                document.getElementById('loginOverlay').style.display = 'flex';
                const savedNick = localStorage.getItem('mySavedNickname');
                if (savedNick) { document.getElementById('nickInput').value = savedNick; document.getElementById('pwInput').focus(); }
            }

            function login() {
                const inputPw = document.getElementById('pwInput').value;
                const inputNick = document.getElementById('nickInput').value.trim();
                if (!inputNick) { alert("닉네임을 적어줘 누나!"); return; }
                if (inputPw === ROOM_PASSWORD) {
                    window.myNickname = inputNick; 
                    window.isAdmin = (inputNick === ADMIN_NICKNAME);
                    localStorage.setItem('mySavedNickname', inputNick);
                    document.getElementById('loginOverlay').style.display = 'none';
                    initCards();
                    connectWebSocket();
                } else { alert("비밀번호가 틀렸어!"); }
            }

            let ws = null;
            let pingInterval = null; 
            const cardData = Array.from({length: 16}, (_, i) => ({ id: i+1, user: `자리${i+1}`, card_bg: null, is_mosaic: false, stopwatch: {is_active: false, is_running: false, start_time: 0, elapsed: 0}, work_start_time: 0 }));
            const myStreams = {}; const peerConnections = {}; const candidateBuffers = {}; 
            const expectedShares = {}; const myOwnedSlots = new Set(); 
            const rtcConfig = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }, { urls: 'stun:stun1.l.google.com:19302' }] };

            function getEmptySlotHTML(username) {
                if (!username || username.startsWith("자리")) return `<div style="position:relative; z-index:2; width:100%; text-align:center;"><span style="font-size:11px; color:#aaa;">화면 미공유 중</span></div>`;
                return `<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; z-index:2; text-align:center; padding:10px; width:100%; height:100%;"><span style="font-size:22px; font-weight:900; color:#fff; text-shadow: 2px 2px 5px rgba(0,0,0,0.9); margin-bottom:4px;">${username}</span><span style="font-size:11px; color:#aaa;">화면 미공유 중</span></div>`;
            }

            function renderBox(index) {
                const box = document.getElementById(`stream-box-${index}`);
                if (!box) return;
                const existingVideo = box.querySelector('video');
                if (existingVideo) existingVideo.remove();
                const card = cardData[index];
                if (card.stopwatch && card.stopwatch.is_active) {
                    const isMine = (card.user === window.myNickname) || window.isAdmin;
                    box.innerHTML = `<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; height:100%;"><div id="sw-display-${index}" style="font-size: 30px; font-weight: 900; font-family: monospace; color: #fff; text-shadow: 2px 2px 6px rgba(0,0,0,0.8);">00:00:00</div>${isMine ? `<div style="margin-top: 8px; display: flex; gap: 4px;"><button onclick="startSw(${index})" style="padding:3px 8px; border:none; border-radius:3px; background:#00b894; color:white; font-weight:bold; cursor:pointer; font-size: 10px;">▶ 시작</button><button onclick="pauseSw(${index})" style="padding:3px 8px; border:none; border-radius:3px; background:#fdcb6e; color:black; font-weight:bold; cursor:pointer; font-size: 10px;">⏸ 정지</button><button onclick="resetSw(${index})" style="padding:3px 8px; border:none; border-radius:3px; background:#d63031; color:white; font-weight:bold; cursor:pointer; font-size: 10px;">⏹ 리셋</button></div>` : ''}</div>`;
                } else { box.innerHTML = getEmptySlotHTML(card.user); }
            }

            function checkWorkTimeStart(index) {
                if (!cardData[index].work_start_time) {
                    const t = Date.now(); cardData[index].work_start_time = t;
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "set_work_time", index: index, time: t }));
                }
            }
            function checkWorkTimeStop(index) {
                const sw = cardData[index].stopwatch;
                if (!myStreams[index] && !(sw && sw.is_active)) {
                    cardData[index].work_start_time = 0;
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "set_work_time", index: index, time: 0 }));
                }
            }

            function toggleStopwatchMode(index) {
                let sw = cardData[index].stopwatch;
                if (!sw) sw = { is_active: false, is_running: false, start_time: 0, elapsed: 0 };
                sw.is_active = !sw.is_active; sw.is_running = false; sw.elapsed = 0; cardData[index].stopwatch = sw;
                if (sw.is_active && myStreams[index]) stopShare(index);
                if (sw.is_active) checkWorkTimeStart(index); else checkWorkTimeStop(index);
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stopwatch_update", index: index, stopwatch: sw }));
                renderBox(index);
            }

            function startSw(index) {
                cardData[index].stopwatch.is_running = true; cardData[index].stopwatch.start_time = Date.now();
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stopwatch_update", index: index, stopwatch: cardData[index].stopwatch }));
            }
            function pauseSw(index) {
                if (!cardData[index].stopwatch.is_running) return;
                cardData[index].stopwatch.is_running = false; cardData[index].stopwatch.elapsed += (Date.now() - cardData[index].stopwatch.start_time);
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stopwatch_update", index: index, stopwatch: cardData[index].stopwatch }));
            }
            function resetSw(index) {
                cardData[index].stopwatch.is_running = false; cardData[index].stopwatch.elapsed = 0;
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stopwatch_update", index: index, stopwatch: cardData[index].stopwatch }));
                renderBox(index);
            }

            setInterval(() => {
                const now = Date.now();
                cardData.forEach((card, idx) => {
                    if (card.stopwatch && card.stopwatch.is_active) {
                        let totalMs = card.stopwatch.elapsed;
                        if (card.stopwatch.is_running) totalMs += (now - card.stopwatch.start_time);
                        const el = document.getElementById(`sw-display-${idx}`);
                        if (el) {
                            let s = Math.floor(totalMs / 1000); let h = Math.floor(s / 3600); s %= 3600; let m = Math.floor(s / 60); s %= 60;
                            el.innerText = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
                        }
                    }
                    const wtEl = document.getElementById(`work-timer-${idx}`);
                    if (wtEl && card.work_start_time) {
                        let s = Math.floor((now - card.work_start_time) / 1000); let h = Math.floor(s / 3600); s %= 3600; let m = Math.floor(s / 60); s %= 60;
                        wtEl.innerText = `⏱ ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
                        wtEl.style.display = 'inline-block';
                    } else if (wtEl) wtEl.style.display = 'none';
                });
            }, 1000);

            function logChat(sender, msg, timeStr) {
                const history = document.getElementById('chatHistory');
                history.innerHTML += `<div style="margin-bottom: 5px;"><b>${sender}</b>: ${msg}<span style="font-size:10px; color:#636e72; margin-left:6px;">${timeStr}</span></div>`;
                history.scrollTop = history.scrollHeight;
            }

            function clearChat() {
                if (confirm("채팅창을 전부 지울까?")) if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "clear_chat" }));
            }

            function forceRecoverWebRTC() {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "request_existing_shares" }));
                    for (let idx in expectedShares) { if (expectedShares[idx] && expectedShares[idx] !== ws.clientId) ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: expectedShares[idx] })); }
                }
                alert("화공 복구 중!");
            }

            function initCards() {
                const grid = document.getElementById('cardGrid'); grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    let bgStyle = card.card_bg ? `background-image: url('${card.card_bg}');` : '';
                    let mosaicBtnBg = card.is_mosaic ? '#e17055' : '#636e72';
                    let mosaicBtnText = card.is_mosaic ? '해제' : '모자이크';
                    grid.innerHTML += `
                        <div class="timer-card" id="card-card-${index}" style="${bgStyle}">
                            <div class="card-header">
                                <div style="display: flex; gap: 4px; align-items: center; width: 100%;">
                                    <input type="text" id="username-${index}" value="${card.user}" style="flex-grow: 1; min-width: 0; padding: 3px; font-size: 11px; font-weight: bold; text-align: center; background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.4); color: white; border-radius: 3px;" oninput="updateUsername(${index}, this.value)">
                                    <span id="work-timer-${index}" style="font-size: 10px; color: #ffeaa7; font-weight: bold; white-space: nowrap; display: ${card.work_start_time ? 'inline-block' : 'none'};">⏱ 00:00:00</span>
                                </div>
                                <div class="btn-group">
                                    <button class="share-btn" id="share-btn-screen-${index}" style="background:#ff7675;" onclick="toggleShare(${index}, 'screen')">화공</button>
                                    <button class="share-btn" id="share-btn-cam-${index}" style="background:#0984e3;" onclick="toggleShare(${index}, 'cam')">캠</button>
                                    <button class="share-btn" id="share-btn-sw-${index}" style="background:#8e44ad;" onclick="toggleStopwatchMode(${index})">시계</button>
                                    <button class="share-btn" id="share-btn-mosaic-${index}" style="background:${mosaicBtnBg};" onclick="handleMosaicClick(${index})">${mosaicBtnText}</button>
                                    <button class="share-btn" id="sound-toggle-btn-${index}" style="background:#00b894; display:none;" onclick="toggleViewerSound(${index})">음소거</button>
                                </div>
                            </div>
                            <div style="display:flex; justify-content:space-between; align-items:center; position:relative; z-index:3; margin-top:2px;">
                                <input type="file" id="card-file-${index}" accept="image/jpeg, image/png, image/webp" style="font-size:9px; width:100%; color:#ccc;" onchange="setCardBackground(${index}, event)">
                            </div>
                            <div class="card-stream-box" id="stream-box-${index}"></div>
                        </div>
                    `;
                });
                document.querySelectorAll('.timer-card').forEach(makeFreeDraggable);
                cardData.forEach((_, i) => renderBox(i));
                applyEmptySlotVisibility();
            }

            function handleMosaicClick(index) {
                const newState = !cardData[index].is_mosaic;
                cardData[index].is_mosaic = newState;
                applyMosaicUI(index, newState);
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "toggle_mosaic", index: index, is_mosaic: newState }));
            }

            function toggleViewerSound(index) {
                const vid = document.getElementById(`remote-video-${index}`);
                const btn = document.getElementById(`sound-toggle-btn-${index}`);
                if (!vid) return;
                vid.muted = !vid.muted;
                btn.innerText = vid.muted ? "소리켜기" : "음소거";
                btn.style.background = vid.muted ? "#b2bec3" : "#00b894";
            }

            function applyMosaicUI(index, isMosaic) {
                const btn = document.getElementById(`share-btn-mosaic-${index}`);
                if (btn) { btn.innerText = isMosaic ? "해제" : "모자이크"; btn.style.background = isMosaic ? "#e17055" : "#636e72"; }
                const remoteVideo = document.getElementById(`remote-video-${index}`);
                const localVideo = document.getElementById(`video-${index}`);
                const activeFilter = isMosaic ? 'blur(5px)' : 'none';
                if (remoteVideo) remoteVideo.style.filter = activeFilter;
                if (localVideo) localVideo.style.filter = activeFilter;
            }

            function updateUsername(index, val) {
                cardData[index].user = val;
                const myName = window.myNickname || "익명";
                if (val === myName) myOwnedSlots.add(index); else myOwnedSlots.delete(index);
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "username_change", index: index, user: val }));
                applyEmptySlotVisibility();
            }

            function setCardBackground(index, event) {
                const file = event.target.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = function(e) {
                    const dataUrl = e.target.result;
                    cardData[index].card_bg = dataUrl;
                    const cardEl = document.getElementById(`card-card-${index}`);
                    if (cardEl) cardEl.style.backgroundImage = `url('${dataUrl}')`;
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "card_bg_change", index: index, dataUrl: dataUrl }));
                };
                reader.readAsDataURL(file);
            }

            function setLocalBackground(event) {
                const file = event.target.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = function(e) {
                    document.getElementById('bgMediaWrapper').innerHTML = `<img src="${e.target.result}" alt="Full Background">`;
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "global_bg_image", dataUrl: e.target.result }));
                };
                reader.readAsDataURL(file);
            }

            async function toggleShare(index, type) {
                const box = document.getElementById(`stream-box-${index}`);
                if (myStreams[index]) { stopShare(index); return; }

                try {
                    let stream = await (type === 'screen' ? navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 15 }, audio: true }) : navigator.mediaDevices.getUserMedia({ video: true, audio: false }));
                    myStreams[index] = stream;
                    box.innerHTML = `<video id="video-${index}" autoplay playsinline muted disablePictureInPicture></video>`;
                    document.getElementById(`video-${index}`).srcObject = stream;
                    checkWorkTimeStart(index);
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "start_share", index: index }));
                    stream.getVideoTracks()[0].onended = () => { stopShare(index); };
                } catch (err) { console.error(err); }
            }

            function stopShare(index) {
                if (myStreams[index]) { myStreams[index].getTracks().forEach(t => t.stop()); delete myStreams[index]; }
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop_share", index: index }));
                checkWorkTimeStop(index);
                renderBox(index);
            }

            function connectWebSocket() {
                const wsUrl = (window.location.protocol === "https:" ? "wss://" : "ws://") + window.location.host + "/ws";
                ws = new WebSocket(wsUrl);
                ws.onopen = function() {
                    document.getElementById('connStatus').innerText = "연결됨";
                    document.getElementById('connStatus').className = "status-indicator status-online";
                    ws.send(JSON.stringify({ type: "set_nickname", nickname: window.myNickname, owned: Array.from(myOwnedSlots) }));
                };
                ws.onmessage = async function(event) {
                    const data = JSON.parse(event.data);
                    if (data.type === "chat") logChat(data.senderName, data.msg, data.time);
                    else if (data.type === "init_state") {
                        const state = data.state;
                        state.cards.forEach((card, i) => {
                            cardData[i].user = card.user; cardData[i].work_start_time = card.work_start_time || 0;
                            const inputEl = document.getElementById(`username-${i}`);
                            if (inputEl) inputEl.value = card.user;
                            renderBox(i);
                        });
                        applyEmptySlotVisibility();
                    }
                    else if (data.type === "start_share") { if (ws.clientId && data.sender !== ws.clientId) ws.send(JSON.stringify({ type: "request_offer", index: data.index, target: data.sender })); }
                    else if (data.type === "offer" && data.target === ws.clientId) {
                        const pc = new RTCPeerConnection(rtcConfig);
                        peerConnections[data.index] = pc;
                        pc.ontrack = (e) => {
                            const box = document.getElementById(`stream-box-${data.index}`);
                            box.innerHTML = `<video id="remote-video-${data.index}" autoplay playsinline disablePictureInPicture></video>`;
                            document.getElementById(`remote-video-${data.index}`).srcObject = e.streams[0];
                        };
                        await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
                        const answer = await pc.createAnswer(); await pc.setLocalDescription(answer);
                        ws.send(JSON.stringify({ type: "answer", index: data.index, target: data.sender, sdp: pc.localDescription }));
                    }
                    else if (data.type === "stop_share") renderBox(data.index);
                };
                ws.onclose = () => { setTimeout(() => window.location.reload(), 3000); };
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
            if p_type == "chat":
                await manager.broadcast(json.dumps(packet))
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
            else:
                packet["sender"] = client_id
                if p_type == "start_share": manager.active_shares[packet["index"]] = client_id
                await manager.broadcast(json.dumps(packet), exclude=websocket)
    except: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
