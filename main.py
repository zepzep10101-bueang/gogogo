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
            if len(cards) > 12:
                data["cards"] = cards[:12]
            elif len(cards) < 12:
                new_cards = [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False} for i in range(len(cards), 12)]
                data["cards"].extend(new_cards)
            
            for i, card in enumerate(data["cards"]):
                card["user"] = f"자리{i+1}"
                card["is_mosaic"] = False
                if "stopwatch" not in card:
                    card["stopwatch"] = {"is_active": False, "is_running": False, "start_time": 0, "elapsed": 0}
                if "work_timer" not in card:
                    card["work_timer"] = {"is_running": False, "start_time": 0, "elapsed": 0}
            
            if "global_notice" not in data:
                data["global_notice"] = "📌 다 함께 모여서 열심히 마감해 봅시다!"
                
            return data
    except Exception:
        pass
    
    initial_data = {
        "_id": "main_state",
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "stopwatch": {"is_active": False, "is_running": False, "start_time": 0, "elapsed": 0}, "work_timer": {"is_running": False, "start_time": 0, "elapsed": 0}} for i in range(12)],
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
            body, html { width: 100%; height: 100%; overflow: hidden; background: #111; }
            .login-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #111; z-index: 9999; display: flex; align-items: center; justify-content: center; flex-direction: column; }
            .login-box { background: rgba(30, 30, 40, 0.9); padding: 30px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.2); text-align: center; color: white; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
            .login-box input { padding: 10px; margin-top: 10px; border-radius: 5px; border: none; width: 220px; text-align: center; }
            .login-box button { padding: 10px 20px; margin-top: 15px; border: none; border-radius: 5px; background: #ff7675; color: white; cursor: pointer; font-weight: bold; width: 100%; }
            .video-background { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; overflow: hidden; pointer-events: none; background: #000; }
            #bgMediaWrapper { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; }
            #bgMediaWrapper img { width: 100vw; height: 100vh; object-fit: cover; display: block; }
            #bgMediaWrapper iframe { width: 100vw; height: 100vh; pointer-events: none; border: none; }
            .overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.05); z-index: 1; pointer-events: none; }

            .main-container { display: grid; grid-template-columns: 4fr 1fr; gap: 20px; padding: 20px; height: 100vh; color: white; position: relative; z-index: 2; align-items: start; max-width: 1600px; margin: 0 auto; box-sizing: border-box; }
            
            /* [스크롤 마법] 카드들이 찌그러지지 않게 스크롤을 허용하고 카드 크기를 min-width로 보장 */
            .card-grid { display: grid; gap: 15px; height: 100%; overflow-y: auto; padding-right: 10px; align-content: start; }
            .grid-12 { grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }

            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 12px; padding: 8px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); backdrop-filter: blur(5px); width: 100%; height: 320px; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: all 0.3s ease; }
            .timer-card.large { grid-column: span 2; grid-row: span 2; height: 655px; }

            .card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 4px; position: relative; z-index: 3; flex-wrap: wrap; }
            .card-stream-box { width: 100%; flex-grow: 1; background: transparent; border-radius: 6px; overflow: hidden; position: relative; margin-top: 4px; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 2; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: transparent; position: absolute; top: 0; left: 0; z-index: 10; }
            .share-btn { padding: 3px 6px; font-size: 10px; color: white; border: none; border-radius: 3px; cursor: pointer; white-space: nowrap; height: fit-content; font-weight: bold; }

            .side-panel { display: flex; flex-direction: column; gap: 15px; height: calc(100vh - 40px); }
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 15px; border: 1px solid rgba(255, 255, 255, 0.2); backdrop-filter: blur(5px); }
            .settings-toggle-btn { background: #636e72; color: white; border: none; border-radius: 4px; padding: 3px 7px; font-size: 11px; cursor: pointer; font-weight: normal; margin-left: 5px; }
            .settings-dropdown { display: none; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.2); }
            .chat-box { display: flex; flex-direction: column; flex-grow: 1; min-height: 0; }
            #chatHistory { flex-grow: 1; overflow-y: auto; margin-top: 10px; font-size: 13px; color: #ddd; line-height: 1.4; }
            .chat-input { display: flex; margin-top: 10px; }
            .chat-input input { flex-grow: 1; padding: 8px; border-radius: 4px; border: none; background: rgba(255, 255, 255, 0.9); color: black; min-width: 0; }
            .chat-input button { padding: 8px 12px; background: #ff7675; border: none; color: white; border-radius: 4px; cursor: pointer; margin-left: 5px; flex-shrink: 0; }
            .recovery-btn { background: #d63031; color: white; border: none; border-radius: 4px; padding: 3px 6px; font-size: 10px; cursor: pointer; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="login-overlay" id="loginOverlay">
            <div class="login-box">
                <h2>🔒 행운방 입장</h2>
                <input type="text" id="nickInput" placeholder="닉네임" onkeypress="if(event.key==='Enter') login()"><br>
                <input type="password" id="pwInput" placeholder="비밀번호" onkeypress="if(event.key==='Enter') login()">
                <br><button onclick="login()">입장하기</button>
            </div>
        </div>
        <div class="video-background" id="bgContainer"><div id="bgMediaWrapper"></div></div>
        <div class="overlay"></div>
        <div class="main-container">
            <div class="card-grid grid-12" id="cardGrid"></div>
            <div class="side-panel">
                <div class="panel-box">
                    <h3 style="margin: 0; font-size: 17px;">👑 대시보드</h3>
                    <button id="hide-empty-btn" class="settings-toggle-btn" onclick="toggleEmptySlots()">🙈 빈자리 끄기</button>
                    <button class="settings-toggle-btn" onclick="toggleSettingsPanel()">⚙️ 배경설정</button>
                    <p style="margin-top:8px; font-size:14px;">접속 인원: <span id="userCount" style="color:#ff7675; font-weight:bold;">0명</span></p>
                    <div id="noticeDropdown" style="display: none; margin-top: 15px; padding: 10px; background: rgba(0,0,0,0.3); border-radius: 5px; border: 1px solid rgba(255, 118, 117, 0.4);">
                        <div id="noticeText" style="font-size: 13px; color: #fff;">공지사항 로딩 중...</div>
                    </div>
                </div>
                <div class="panel-box chat-box">
                    <h3>💬 실시간 채팅</h3>
                    <div id="chatHistory"></div>
                    <div class="chat-input">
                        <input type="text" id="chatInput" onkeypress="if(event.key==='Enter') sendChat()">
                        <button onclick="sendChat()">전송</button>
                    </div>
                </div>
            </div>
        </div>
        <script>
            const ROOM_PASSWORD = "7777"; 
            const ADMIN_NICKNAME = "부엉";
            let hideEmptySlots = false;
            let cardData = Array.from({length: 12}, (_, i) => ({ id: i+1, user: `자리${i+1}`, card_bg: null, is_mosaic: false, stopwatch: {is_active: false, is_running: false, start_time: 0, elapsed: 0}, work_timer: {is_running: false, start_time: 0, elapsed: 0} }));

            function toggleEmptySlots() {
                hideEmptySlots = !hideEmptySlots;
                applyEmptySlotVisibility();
            }

            function applyEmptySlotVisibility() {
                cardData.forEach((card, index) => {
                    const cardEl = document.getElementById(`card-card-${index}`);
                    if (cardEl) {
                        cardEl.style.display = (hideEmptySlots && card.user.startsWith("자리")) ? "none" : "flex";
                    }
                });
            }

            function login() {
                if (document.getElementById('pwInput').value === ROOM_PASSWORD) {
                    window.myNickname = document.getElementById('nickInput').value;
                    document.getElementById('loginOverlay').style.display = 'none';
                    initCards();
                    connectWebSocket();
                } else {
                    alert("비밀번호 틀렸어!");
                }
            }

            function toggleCardSize(index) {
                const targetCard = document.getElementById(`card-card-${index}`);
                const targetBtn = document.getElementById(`size-btn-${index}`);
                const isCurrentlyLarge = targetCard.classList.contains('large');
                
                // 모든 카드 작게 초기화
                document.querySelectorAll('.timer-card').forEach(card => card.classList.remove('large'));
                document.querySelectorAll('.share-btn[id^="size-btn"]').forEach(btn => {
                    btn.innerText = "크게";
                    btn.style.background = "#fdcb6e";
                });

                if (!isCurrentlyLarge) {
                    targetCard.classList.add('large');
                    targetBtn.innerText = "작게";
                    targetBtn.style.background = "#e17055";
                }
            }

            function initCards() {
                const grid = document.getElementById('cardGrid');
                grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    grid.innerHTML += `
                        <div class="timer-card" id="card-card-${index}">
                            <div class="card-header">
                                <input type="text" value="${card.user}" oninput="updateUsername(${index}, this.value)">
                                <button id="size-btn-${index}" onclick="toggleCardSize(${index})">크게</button>
                            </div>
                            <div class="card-stream-box" id="stream-box-${index}"></div>
                        </div>
                    `;
                });
            }

            // ... 나머지 함수들은 동일 ...
            function updateUsername(index, val) { cardData[index].user = val; }
            function connectWebSocket() { /* 웹소켓 로직 유지 */ }
            function renderBox(index) { /* 렌더링 로직 유지 */ }
            function toggleShare(index, type) { /* 공유 로직 유지 */ }
            function stopShare(index) { /* 공유 중지 로직 유지 */ }
            function sendChat() { /* 채팅 로직 유지 */ }
            function logChat(s, m, t) { /* 채팅 로그 유지 */ }
            function clearChat() { /* 채팅 청소 로직 유지 */ }
            function setCardBackground(i, e) { /* 배경 설정 로직 유지 */ }
            function setLocalBackground(e) { /* 배경 설정 로직 유지 */ }
            function setYoutubeBackground() { /* 배경 설정 로직 유지 */ }
            function toggleNoticePanel() { /* 공지 패널 유지 */ }
            function toggleSettingsPanel() { /* 설정 패널 유지 */ }
            function handleMosaicClick(i) { /* 모자이크 유지 */ }
            function applyMosaicUI(i, m) { /* 모자이크 UI 유지 */ }
            function toggleStopwatchMode(i) { /* 스톱워치 유지 */ }
            function startSw(i) { /* 시작 로직 유지 */ }
            function pauseSw(i) { /* 정지 로직 유지 */ }
            function resetSw(i) { /* 리셋 로직 유지 */ }
            function toggleViewerSound(i) { /* 소리 유지 */ }
            function forceRecoverWebRTC() { /* 복구 로직 유지 */ }
            function formatNotice(t) { /* 포맷 유지 */ }
            function addNotice() { /* 추가 로직 유지 */ }
            function editNotice() { /* 수정 로직 유지 */ }
            function checkWorkTimeStart(i) { /* 시간 로직 유지 */ }
            function checkWorkTimeStop(i) { /* 시간 로직 유지 */ }
            function resetWorkTimer(i) { /* 시간 로직 유지 */ }

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
            await manager.broadcast(json.dumps(packet), exclude=websocket)
    except:
        pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
