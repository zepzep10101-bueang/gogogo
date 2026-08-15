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
        if websocket in self.active_connections: self.active_connections.remove(websocket)
        if websocket in self.active_users: del self.active_users[websocket]
        disconnected_client = str(id(websocket))
        freed_indexes = []
        to_remove = [idx for idx, cid in self.active_shares.items() if cid == disconnected_client]
        for idx in to_remove:
            del self.active_shares[idx]
            freed_indexes.append(idx)
        return freed_indexes

    async def broadcast(self, message: str, exclude: WebSocket = None):
        for connection in list(self.active_connections):
            if connection != exclude:
                try: await connection.send_text(message)
                except: pass

    async def broadcast_user_list(self):
        users = [name for name in self.active_users.values() if name != "연결중..."]
        msg = json.dumps({"type": "user_list", "count": len(self.active_connections), "users": users})
        for conn in self.active_connections:
            try: await conn.send_text(msg)
            except: pass

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
            .main-container { display: grid; grid-template-columns: 5fr 240px; gap: 20px; padding: 20px; min-height: 100vh; color: white; position: relative; z-index: 2; align-items: start; max-width: 1800px; margin: 0 auto; }
            .card-grid { display: grid; gap: 15px; align-content: start; justify-content: center; width: 100%; }
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 12px; padding: 8px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); backdrop-filter: blur(5px); min-width: 250px; min-height: 250px; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: box-shadow 0.3s ease; resize: both; }
            .card-header { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 3; width: 100%; cursor: move; }
            .btn-group { display: flex; gap: 2px; width: 100%; justify-content: center; flex-wrap: nowrap; }
            .share-btn { padding: 4px 2px; font-size: 10px; color: white; border: none; border-radius: 3px; cursor: pointer; white-space: nowrap; font-weight: bold; text-align: center; flex-grow: 1; }
            .card-stream-box { width: 100%; flex-grow: 1; min-height: 150px; background: rgba(0, 0, 0, 0.15); border-radius: 8px; overflow: hidden; position: relative; margin-top: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 2; pointer-events: none; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: transparent; position: absolute; top: 0; left: 0; z-index: 10; transition: filter 0.2s ease-in-out; pointer-events: auto; }
            .side-panel { display: flex; flex-direction: column; gap: 15px; position: sticky; top: 20px; height: calc(100vh - 40px); }
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 12px; border: 1px solid rgba(255, 255, 255, 0.2); backdrop-filter: blur(5px); min-width: 0; }
            .settings-toggle-btn { background: #636e72; color: white; border: none; border-radius: 4px; padding: 3px 6px; font-size: 11px; cursor: pointer; margin-left: 2px; }
            .chat-box { display: flex; flex-direction: column; flex-grow: 1; min-height: 0; height: 100%; }
            #chatHistory { flex-grow: 1; overflow-y: auto; margin-top: 8px; font-size: 13px; color: #ddd; line-height: 1.5; padding-right: 4px; }
            .chat-input { display: flex; margin-top: 8px; gap: 4px; }
            .chat-input input { flex-grow: 1; padding: 7px; border-radius: 4px; border: none; background: rgba(255, 255, 255, 0.9); color: black; font-size: 12px; }
            .chat-input button { padding: 7px 10px; background: #ff7675; border: none; color: white; border-radius: 4px; cursor: pointer; font-size: 12px; }
            .status-indicator { font-size: 11px; padding: 2px 6px; border-radius: 3px; display: inline-block; margin-left: 5px; }
            .status-online { background: #00b894; color: white; }
            .status-offline { background: #d63031; color: white; }
        </style>
    </head>
    <body>
        <div class="login-overlay" id="loginOverlay"><div class="login-box"><h2>🔒 행운방 입장</h2><input type="text" id="nickInput" placeholder="닉네임"><br><input type="password" id="pwInput" placeholder="비밀번호"><br><button onclick="login()">입장하기</button></div></div>
        <div class="video-background" id="bgContainer"><div id="bgMediaWrapper"></div></div>
        <div class="main-container">
            <div class="card-grid" id="cardGrid"></div>
            <div class="side-panel">
                <div class="panel-box"><h3 style="margin: 0; font-size: 15px;">👑 대시보드</h3><span id="connStatus" class="status-indicator status-offline">연결 중...</span><p style="margin-top:6px; font-size:13px;">인원: <span id="userCount" style="color:#ff7675; font-weight:bold;">0명</span></p><div id="userListStr" style="display:flex; flex-wrap:wrap; gap:4px; margin-top:2px;"></div></div>
                <div class="panel-box chat-box"><h3 style="font-size: 14px;">💬 채팅</h3><div id="chatHistory"></div><div class="chat-input"><input type="text" id="chatInput" placeholder="메시지..." onkeypress="if(event.key==='Enter') sendChat()"><button onclick="sendChat()">전송</button></div></div>
            </div>
        </div>
        <script>
            const ROOM_PASSWORD = "7777";
            let ws = null;
            const cardData = Array.from({length: 16}, (_, i) => ({ id: i+1, user: `자리${i+1}`, stopwatch: {is_active: false}, work_start_time: 0 }));
            
            function makeFreeDraggable(el) {
                let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
                const header = el.querySelector('.card-header');
                if (header) header.onmousedown = dragMouseDown;
                function dragMouseDown(e) {
                    if (['input', 'button'].includes(e.target.tagName.toLowerCase())) return;
                    e.preventDefault();
                    pos3 = e.clientX; pos4 = e.clientY;
                    document.onmouseup = () => { document.onmouseup = null; document.onmousemove = null; };
                    document.onmousemove = (e) => {
                        e.preventDefault();
                        pos1 = pos3 - e.clientX; pos2 = pos4 - e.clientY;
                        pos3 = e.clientX; pos4 = e.clientY;
                        el.style.top = (el.offsetTop - pos2) + "px"; el.style.left = (el.offsetLeft - pos1) + "px";
                    };
                    if (el.style.position !== 'fixed') {
                        const rect = el.getBoundingClientRect();
                        el.style.position = 'fixed'; el.style.zIndex = '9999'; el.style.left = rect.left + 'px'; el.style.top = rect.top + 'px';
                    }
                }
            }

            function initCards() {
                const grid = document.getElementById('cardGrid'); grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    grid.innerHTML += `
                        <div class="timer-card" id="card-card-${index}">
                            <div class="card-header">
                                <div style="display: flex; gap: 4px; align-items: center; width: 100%;">
                                    <input type="text" value="${card.user}" style="flex-grow: 1; padding: 3px; font-size: 11px; text-align: center; background: rgba(255,255,255,0.2); border: none; color: white; border-radius: 3px;" oninput="updateUsername(${index}, this.value)">
                                    <span id="work-timer-${index}" style="font-size: 10px; color: #ffeaa7; font-weight: bold; white-space: nowrap; display: none;">⏱ 00:00:00</span>
                                </div>
                                <div class="btn-group">
                                    <button class="share-btn" style="background:#ff7675;" onclick="toggleShare(${index}, 'screen')">화공</button>
                                </div>
                            </div>
                            <div class="card-stream-box" id="stream-box-${index}"></div>
                        </div>`;
                });
                document.querySelectorAll('.timer-card').forEach(makeFreeDraggable);
            }

            function login() { if (document.getElementById('pwInput').value === ROOM_PASSWORD) { window.myNickname = document.getElementById('nickInput').value; document.getElementById('loginOverlay').style.display = 'none'; initCards(); connectWebSocket(); } else alert("비밀번호 틀림!"); }
            function updateUsername(index, val) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "username_change", index: index, user: val })); }
            function connectWebSocket() {
                const wsUrl = (window.location.protocol === "https:" ? "wss://" : "ws://") + window.location.host + "/ws";
                ws = new WebSocket(wsUrl);
                ws.onmessage = function(event) {
                    const data = JSON.parse(event.data);
                    if (data.type === "init_state") {
                        data.state.cards.forEach((c, i) => {
                            cardData[i].user = c.user;
                            const el = document.getElementById(`card-card-${i}`);
                            if(el) {
                                const input = el.querySelector('input');
                                if(input) input.value = c.user;
                                el.style.display = c.user.startsWith("자리") ? "none" : "flex";
                            }
                        });
                    }
                };
            }
            function sendChat() { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "chat", senderName: window.myNickname, msg: document.getElementById('chatInput').value, time: "방금" })); }
        </script>
    </body>
    </html>
    """

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    await websocket.send_text(json.dumps({"type": "init_state", "state": server_state}))
    try:
        while True:
            data = await websocket.receive_text()
            packet = json.loads(data)
            if packet["type"] == "chat": await manager.broadcast(json.dumps(packet))
            else: await manager.broadcast(json.dumps(packet), exclude=websocket)
    except: pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
