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
                
            return data
    except Exception:
        pass
    
    initial_data = {
        "_id": "main_state",
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False} for i in range(12)],
        "global_bg": None,
        "global_bg_type": None,
        "chat_history": []
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

            .main-container { display: grid; grid-template-columns: 4fr 1fr; gap: 20px; padding: 20px; min-height: 100vh; color: white; position: relative; z-index: 2; align-items: start; max-width: 1600px; margin: 0 auto; }
            
            .card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 15px; align-content: start; grid-auto-flow: dense; }
            
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 12px; padding: 10px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); backdrop-filter: blur(5px); aspect-ratio: 4 / 5; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: all 0.3s ease; }
            
            .timer-card.large { grid-column: span 2; grid-row: span 2; }

            .card-header { display: flex; justify-content: space-between; align-items: center; gap: 5px; position: relative; z-index: 3; flex-wrap: wrap; }
            
            .card-stream-box { width: 100%; flex-grow: 1; background: rgba(0, 0, 0, 0.65); border-radius: 8px; overflow: hidden; position: relative; margin-top: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 1px solid rgba(255,255,255,0.2); z-index: 2; }
            
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: #000; position: absolute; top: 0; left: 0; z-index: 10; transition: filter 0.2s ease-in-out; }
            
            .share-btn { padding: 4px 6px; font-size: 11px; color: white; border: none; border-radius: 4px; cursor: pointer; white-space: nowrap; }

            .side-panel { display: flex; flex-direction: column; gap: 15px; position: sticky; top: 20px; }
            
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 15px; border: 1px solid rgba(255, 255, 255, 0.2); backdrop-filter: blur(5px); }
            
            .settings-toggle-btn { background: #636e72; color: white; border: none; border-radius: 4px; padding: 3px 7px; font-size: 11px; cursor: pointer; float: right; font-weight: normal; margin-left: 5px; }
            .settings-dropdown { display: none; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.2); }

            .chat-box { display: flex; flex-direction: column; height: 500px; }
            #chatHistory { flex-grow: 1; overflow-y: auto; margin-top: 10px; font-size: 13px; color: #ddd; line-height: 1.4; }
            .chat-input { display: flex; margin-top: 10px; }
            .chat-input input { flex-grow: 1; padding: 8px; border-radius: 4px; border: none; background: rgba(255, 255, 255, 0.9); color: black; min-width: 0; }
            .chat-input button { padding: 8px 12px; background: #ff7675; border: none; color: white; border-radius: 4px; cursor: pointer; margin-left: 5px; flex-shrink: 0; }
            
            .bg-control { display: flex; flex-direction: column; gap: 6px; margin-top: 5px; }
            .status-indicator { font-size: 11px; padding: 2px 6px; border-radius: 3px; display: inline-block; margin-left: 5px; }
            .status-online { background: #00b894; color: white; }
            .status-offline { background: #d63031; color: white; }
            
            .recovery-btn { background: #d63031; color: white; border: none; border-radius: 4px; padding: 3px 6px; font-size: 10px; cursor: pointer; font-weight: bold; }
        </style>
    </head>
    <body>

        <svg xmlns="http://www.w3.org/2000/svg" version="1.1" style="position:absolute; width:0; height:0; display:none;">
          <defs>
            <filter id="relative-blur" primitiveUnits="objectBoundingBox">
              <feGaussianBlur stdDeviation="0.008 0.008" />
            </filter>
          </defs>
        </svg>

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
                <div class="panel-box">
                    <h3>
                        👑 대시보드 
                        <button class="settings-toggle-btn" onclick="toggleSettingsPanel()">⚙️ 배경설정</button>
                        <button id="hide-empty-btn" class="settings-toggle-btn" onclick="toggleEmptySlots()">🙈 빈자리 숨기기</button>
                    </h3>
                    <span id="connStatus" class="status-indicator status-offline" style="margin-top:5px;">연결 중...</span>
                    <p style="margin-top:8px; font-size:14px;">현재 접속 인원: <span id="userCount" style="color:#ff7675; font-weight:bold;">0명</span></p>
                    
                    <p style="margin-top:5px; font-size:12px; color:#aaa; line-height:1.6;">접속자 명단:<br><span id="userListStr" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:4px;"></span></p>
                    
                    <div class="settings-dropdown" id="settingsDropdown">
                        <div style="font-size: 11px; font-weight: bold; color: #fff; margin-bottom: 4px;">🖼️ 전체 배경 꾸미기</div>
                        <div class="bg-control">
                            <div style="font-size: 10px; color: #aaa;">일반 사진 선택 (움짤X):</div>
                            <input type="file" id="bgFileInput" accept="image/jpeg, image/png, image/webp" style="font-size:10px; width:100%;" onchange="setLocalBackground(event)">
                            
                            <div style="font-size: 10px; color: #aaa; margin-top: 3px;">유튜브 링크 입력:</div>
                            <div style="display:flex; gap:3px;">
                                <input type="text" id="bgYoutubeInput" placeholder="유튜브 URL" style="flex-grow:1; font-size:10px; padding:3px; background:rgba(255,255,255,0.9); color:black; border:none; border-radius:3px; min-width:0;">
                                <button onclick="setYoutubeBackground()" style="font-size:10px; padding:3px 6px; background:#ff7675; border:none; color:white; border-radius:3px; cursor:pointer;">적용</button>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="panel-box chat-box">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h3 style="font-size: 15px;">💬 실시간 채팅</h3>
                        <div>
                            <button onclick="forceRecoverWebRTC()" class="recovery-btn">🔄 화공복구</button>
                            <button onclick="clearChat()" style="font-size:10px; padding:3px 5px; background:#636e72; border:none; color:white; border-radius:3px; cursor:pointer; margin-left:2px;">청소</button>
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
            // [여기서 비밀번호 변경!] 누나가 원하는 비밀번호로 수정해!
            const ROOM_PASSWORD = "7777"; 
            const ADMIN_NICKNAME = "부엉";

            let hideEmptySlots = false;

            function toggleEmptySlots() {
                hideEmptySlots = !hideEmptySlots;
                const btn = document.getElementById('hide-empty-btn');
                if (hideEmptySlots) {
                    btn.innerText = "🐵 빈자리 보이기";
                    btn.style.background = "#0984e3";
                } else {
                    btn.innerText = "🙈 빈자리 숨기기";
                    btn.style.background = "#636e72";
                }
                applyEmptySlotVisibility();
            }

            function applyEmptySlotVisibility() {
                cardData.forEach((card, index) => {
                    const cardEl = document.getElementById(`card-card-${index}`);
                    if (cardEl) {
                        if (hideEmptySlots && card.user.startsWith("자리")) {
                            cardEl.style.display = "none";
                        } else {
                            cardEl.style.display = "flex";
                        }
                    }
                });
            }

            function toggleSettingsPanel() {
                const dropdown = document.getElementById('settingsDropdown');
                if (dropdown.style.display === 'block') {
                    dropdown.style.display = 'none';
                } else {
                    dropdown.style.display = 'block';
                }
            }

            // [수정된 마법 1] 닉네임 자동 불러오기
            function checkLogin() {
                document.getElementById('loginOverlay').style.display = 'flex';
                
                const savedNick = localStorage.getItem('mySavedNickname');
                if (savedNick) {
                    document.getElementById('nickInput').value = savedNick;
                    document.getElementById('pwInput').focus();
                }
            }

            // [수정된 마법 2] 로그인 시 닉네임 영구 저장하기
            function login() {
                const inputPw = document.getElementById('pwInput').value;
                const inputNick = document.getElementById('nickInput').value.trim();
                
                if (!inputNick) {
                    alert("누군지 알 수 있게 닉네임을 적어줘 누나!");
                    return;
                }

                if (inputPw === ROOM_PASSWORD) {
                    window.myNickname = inputNick; 
                    window.isAdmin = (inputNick === ADMIN_NICKNAME);
                    
                    localStorage.setItem('mySavedNickname', inputNick);
                    
                    document.getElementById('loginOverlay').style.display = 'none';
                    initCards();
                    connectWebSocket();
                } else {
                    alert("비밀번호가 틀렸어! 다시 확인해봐.");
                }
            }

            let ws = null;
            let pingInterval = null; 
            
            const cardData = Array.from({length: 12}, (_, i) => ({ id: i+1, user: `자리${i+1}`, card_bg: null, is_mosaic: false }));
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
                        <span style="font-size:26px; font-weight:900; color:#fff; text-shadow: 2px 2px 5px rgba(0,0,0,0.9); margin-bottom:8px;">${username}</span>
                        <span style="font-size:12px; color:#aaa;">화면 미공유 중</span>
                    </div>`;
                }
            }

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
            
            function toggleCardSize(index) {
                const card = document.getElementById(`card-card-${index}`);
                const btn = document.getElementById(`size-btn-${index}`);
                
                if (card.classList.contains('large')) {
                    card.classList.remove('large');
                    btn.innerText = "크게";
                    btn.style.background = "#fdcb6e";
                } else {
                    card.classList.add('large');
                    btn.innerText = "작게";
                    btn.style.background = "#e17055";
                }
            }

            function initCards() {
                const grid = document.getElementById('cardGrid');
                grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    let bgStyle = card.card_bg ? `background-image: url('${card.card_bg}');` : '';
                    let mosaicBtnBg = card.is_mosaic ? '#e17055' : '#636e72';
                    let mosaicBtnText = card.is_mosaic ? '모자이크 해제' : '모자이크';

                    grid.innerHTML += `
                        <div class="timer-card" id="card-card-${index}" style="${bgStyle}">
                            <div class="card-header">
                                <input type="text" id="username-${index}" value="${card.user}" style="flex-grow:1; min-width:0; padding:4px; font-size:12px; font-weight:bold; text-align:center; background:rgba(255,255,255,0.2); border:1px solid rgba(255,255,255,0.4); color:white; border-radius:3px;" oninput="updateUsername(${index}, this.value)">
                                
                                <div style="display:flex; gap:3px;">
                                    <button class="share-btn" id="share-btn-screen-${index}" style="background:#ff7675;" onclick="toggleShare(${index}, 'screen')">화공</button>
                                    <button class="share-btn" id="share-btn-cam-${index}" style="background:#0984e3;" onclick="toggleShare(${index}, 'cam')">캠</button>
                                    <button class="share-btn" id="share-btn-mosaic-${index}" style="background:${mosaicBtnBg};" onclick="handleMosaicClick(${index})">${mosaicBtnText}</button>
                                    
                                    <button class="share-btn" id="size-btn-${index}" style="background:#fdcb6e; color:black;" onclick="toggleCardSize(${index})">크게</button>
                                    
                                    <button class="share-btn" id="sound-toggle-btn-${index}" style="background:#00b894; display:none;" onclick="toggleViewerSound(${index})">소리끄기</button>
                                </div>
                            </div>
                            
                            <div style="display:flex; justify-content:space-between; align-items:center; position:relative; z-index:3; margin-top:4px;">
                                <input type="file" id="card-file-${index}" accept="image/jpeg, image/png, image/webp" style="font-size:10px; width:100%; color:#ccc;" onchange="setCardBackground(${index}, event)">
                            </div>

                            <div class="card-stream-box" id="stream-box-${index}">
                                ${getEmptySlotHTML(card.user)}
                            </div>
                        </div>
                    `;
                });
                
                applyEmptySlotVisibility();
            }

            function handleMosaicClick(index) {
                const isMyStream = !!myStreams[index];
                
                if (!isMyStream && !window.isAdmin) {
                    alert("본인이 화면을 공유 중일 때만 모자이크를 조작할 수 있어!");
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
                    btn.innerText = "소리끄기";
                    btn.style.background = "#00b894"; 
                }
            }

            function applyMosaicUI(index, isMosaic) {
                const btn = document.getElementById(`share-btn-mosaic-${index}`);
                if (btn) {
                    btn.innerText = isMosaic ? "모자이크 해제" : "모자이크";
                    btn.style.background = isMosaic ? "#e17055" : "#636e72";
                }

                const remoteVideo = document.getElementById(`remote-video-${index}`);
                const localVideo = document.getElementById(`video-${index}`);
                
                const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
                const activeFilter = isMosaic ? (isMobile ? 'blur(3px)' : 'url(#relative-blur)') : 'none';

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
                    box.innerHTML = getEmptySlotHTML(val);
                }

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "username_change", index: index, user: val }));
                }
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
                    
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "global_bg_image", dataUrl: dataUrl }));
                    }
                };
                reader.readAsDataURL(file);
            }

            async function toggleShare(index, type) {
                const box = document.getElementById(`stream-box-${index}`);
                const btnScreen = document.getElementById(`share-btn-screen-${index}`);
                const btnCam = document.getElementById(`share-btn-cam-${index}`);
                
                if (myStreams[index]) {
                    stopShare(index);
                    return;
                }

                try {
                    let stream;
                    if (type === 'screen') {
                        stream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: "always", frameRate: 30 }, audio: true });
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

                    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
                    const activeFilter = isMobile ? 'blur(3px)' : 'url(#relative-blur)';
                    let filterStyle = cardData[index].is_mosaic ? `filter: ${activeFilter};` : '';
                    
                    box.innerHTML = `<video id="video-${index}" autoplay playsinline muted disablePictureInPicture style="${filterStyle}"></video>`;
                    const localVideo = document.getElementById(`video-${index}`);
                    localVideo.srcObject = stream;
                    localVideo.play().catch(e => console.log(e));

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

                cardData[index].is_mosaic = false;
                applyMosaicUI(index, false);
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "toggle_mosaic", index: index, is_mosaic: false }));
                }

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "stop_share", index: index }));
                }

                const box = document.getElementById(`stream-box-${index}`);
                box.innerHTML = getEmptySlotHTML(cardData[index].user);
                
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

            function setYoutubeBackground() {
                const inputVal = document.getElementById('bgYoutubeInput').value;
                const videoId = extractYoutubeId(inputVal);
                if (!videoId) { alert("유튜브 링크가 올바르지 않습니다."); return; }

                document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&loop=1&playlist=${videoId}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
                
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "global_bg_youtube", videoId: videoId }));
                }
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
                            
                            else if (data.type === "chat_cleared") {
                                document.getElementById('chatHistory').innerHTML = "";
                            }
                            
                            else if (data.type === "user_list") {
                                document.getElementById('userCount').innerText = data.count + "명";
                                
                                let listHtml = data.users.map(u => 
                                    `<span style="background:rgba(255,255,255,0.1); padding:3px 8px; border-radius:4px; display:inline-block;">
                                        <b style="color:white;">${u}</b>
                                    </span>`
                                ).join("");
                                document.getElementById('userListStr').innerHTML = listHtml;
                            }
                            else if (data.type === "chat") {
                                logChat(data.senderName, data.msg, data.time);
                            } 
                            else if (data.type === "init_state") {
                                const state = data.state;
                                if (state.cards) {
                                    state.cards.forEach((card, i) => {
                                        if (cardData[i]) {
                                            cardData[i].user = card.user;
                                            cardData[i].card_bg = card.card_bg;
                                            cardData[i].is_mosaic = card.is_mosaic || false;
                                            applyMosaicUI(i, cardData[i].is_mosaic);

                                            const userEl = document.getElementById(`username-${i}`);
                                            if (userEl) userEl.value = card.user;
                                            const cardEl = document.getElementById(`card-card-${i}`);
                                            if (cardEl && card.card_bg) { cardEl.style.backgroundImage = `url('${card.card_bg}')`; }
                                            
                                            const box = document.getElementById(`stream-box-${i}`);
                                            if (box && !box.querySelector('video')) {
                                                box.innerHTML = getEmptySlotHTML(card.user);
                                            }
                                        }
                                    });
                                }
                                
                                if (state.global_bg_type === "image" && state.global_bg) {
                                    document.getElementById('bgMediaWrapper').innerHTML = `<img src="${state.global_bg}" alt="Full Background">`;
                                } else if (state.global_bg_type === "youtube" && state.global_bg) {
                                    document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${state.global_bg}?autoplay=1&mute=1&loop=1&playlist=${state.global_bg}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
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
                                    box.innerHTML = getEmptySlotHTML(data.user);
                                }
                                
                                applyEmptySlotVisibility();
                                
                            } else if (data.type === "card_bg_change") {
                                cardData[data.index].card_bg = data.dataUrl;
                                const cardEl = document.getElementById(`card-card-${data.index}`);
                                if (cardEl) { cardEl.style.backgroundImage = `url('${data.dataUrl}')`; }
                            } else if (data.type === "global_bg_image") {
                                document.getElementById('bgMediaWrapper').innerHTML = `<img src="${data.dataUrl}" alt="Full Background">`;
                            } else if (data.type === "global_bg_youtube") {
                                document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${data.videoId}?autoplay=1&mute=1&loop=1&playlist=${data.videoId}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
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
                                    
                                    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
                                    const activeFilter = isMobile ? 'blur(3px)' : 'url(#relative-blur)';
                                    let filterStyle = cardData[index].is_mosaic ? `filter: ${activeFilter};` : '';
                                    
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
                                const box = document.getElementById(`stream-box-${index}`);
                                box.innerHTML = getEmptySlotHTML(cardData[index].user);
                                
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
                        statusEl.innerText = "연결 끊김";
                        statusEl.className = "status-indicator status-offline";
                        setTimeout(connectWebSocket, 2000);
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

                recovered = False
                if owned:
                    for idx in owned:
                        if 0 <= idx < 12:
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
                elif p_type == "global_bg_image":
                    server_state["global_bg"] = packet.get("dataUrl")
                    server_state["global_bg_type"] = "image"
                    asyncio.create_task(asyncio.to_thread(save_data, server_state))
                elif p_type == "global_bg_youtube":
                    server_state["global_bg"] = packet.get("videoId")
                    server_state["global_bg_type"] = "youtube"
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
                
                await manager.broadcast(json.dumps(packet), exclude=websocket)

    except (WebSocketDisconnect, Exception):
        client_id = str(id(websocket))
        
        reverted_indexes = []
        if client_id in manager.active_slots:
            for r_idx in manager.active_slots[client_id]:
                # [해결책 코드] 유령 연결(Ghost Check) 검사!
                is_claimed_by_other = False
                for other_cid, slots in manager.active_slots.items():
                    if other_cid != client_id and r_idx in slots:
                        is_claimed_by_other = True
                        break
                
                # 아무도 안 쓰고 있을 때만 빈자리로 초기화!
                if not is_claimed_by_other:
                    server_state["cards"][r_idx]["user"] = f"자리{r_idx+1}"
                    reverted_indexes.append(r_idx)
                    
            del manager.active_slots[client_id]
            asyncio.create_task(asyncio.to_thread(save_data, server_state))

        freed_indexes = manager.disconnect(websocket)
        await manager.broadcast_user_list()
        
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
