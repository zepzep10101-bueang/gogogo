from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
json = __import__('json')
os = __import__('os')
uvicorn = __import__('uvicorn')
asyncio = __import__('asyncio')

from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "여기에_누나의_망고로드_주소를_넣어주면_돼")

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
    
    return {
        "_id": "main_state",
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None} for i in range(8)],
        "global_bg": None,
        "global_bg_type": None,
        "chat_history": []
    }

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
        <title>통합</title>
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
            .overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.35); z-index: 1; pointer-events: none; }

            .main-container { display: grid; grid-template-columns: 3fr 1fr; gap: 20px; padding: 20px; min-height: 100vh; color: white; position: relative; z-index: 2; }
            .card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 15px; align-content: start; }
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 12px; padding: 10px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); backdrop-filter: blur(5px); aspect-ratio: 4 / 5; position: relative; overflow: hidden; background-size: cover; background-position: center; }
            .card-header { display: flex; justify-content: space-between; align-items: center; gap: 5px; position: relative; z-index: 3; }
            
            .card-stream-box { width: 100%; flex-grow: 1; background: rgba(0, 0, 0, 0.65); border-radius: 8px; overflow: hidden; position: relative; margin-top: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 1px solid rgba(255,255,255,0.2); z-index: 2; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: #000; position: absolute; top: 0; left: 0; }
            .share-btn { padding: 4px 6px; font-size: 11px; color: white; border: none; border-radius: 4px; cursor: pointer; white-space: nowrap; }

            .side-panel { display: flex; flex-direction: column; gap: 15px; }
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 15px; border: 1px solid rgba(255, 255, 255, 0.2); backdrop-filter: blur(5px); }
            .chat-box { flex-grow: 1; display: flex; flex-direction: column; justify-content: flex-end; }
            .chat-input { display: flex; margin-top: 10px; }
            .chat-input input { flex-grow: 1; padding: 8px; border-radius: 4px; border: none; background: rgba(255, 255, 255, 0.9); color: black; }
            .chat-input button { padding: 8px 15px; background: #ff7675; border: none; color: white; border-radius: 4px; cursor: pointer; margin-left: 5px; }
            
            .bg-control { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
            .status-indicator { font-size: 11px; padding: 2px 6px; border-radius: 3px; display: inline-block; margin-left: 5px; }
            .status-online { background: #00b894; color: white; }
            .status-offline { background: #d63031; color: white; }
        </style>
    </head>
    <body>

        <div class="login-overlay" id="loginOverlay">
            <div class="login-box">
                <h2>🔒 행운방 입장</h2>
                <p style="font-size: 13px; color: #aaa; margin-top: 5px; margin-bottom: 15px;">매번 접속할 때마다 닉네임과 비밀번호를 적어줘!</p>
                <input type="text" id="nickInput" placeholder="내 닉네임 (예: 디오)" onkeypress="if(event.key==='Enter') login()"><br>
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
                    <h3>👑 대시보드 <span id="connStatus" class="status-indicator status-offline">연결 중...</span></h3>
                    <p style="margin-top:8px; font-size:14px;">현재 접속 인원: <span id="userCount" style="color:#ff7675; font-weight:bold;">0명</span></p>
                    
                    <p style="margin-top:5px; font-size:12px; color:#aaa; line-height:1.6;">접속자 명단:<br><span id="userListStr" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:4px;"></span></p>
                </div>

                <div class="panel-box">
                    <h3>🖼️ 나만의 전체 배경 설정</h3>
                    <div class="bg-control">
                        <div style="font-size: 11px; color: #aaa;">GIF/이미지 파일 선택:</div>
                        <input type="file" id="bgFileInput" accept="image/*" style="font-size:11px;" onchange="setLocalBackground(event)">
                        
                        <div style="font-size: 11px; color: #aaa; margin-top: 5px;">유튜브 링크 입력:</div>
                        <div style="display:flex; gap:4px;">
                            <input type="text" id="bgYoutubeInput" placeholder="유튜브 URL 붙여넣기" style="flex-grow:1; font-size:11px; padding:4px; background:rgba(255,255,255,0.9); color:black; border:none; border-radius:3px;">
                            <button onclick="setYoutubeBackground()" style="font-size:11px; padding:4px 8px; background:#ff7675; border:none; color:white; border-radius:3px; cursor:pointer;">적용</button>
                        </div>
                    </div>
                </div>

                <div class="panel-box chat-box">
                    <h3>💬 실시간 채팅</h3>
                    <div id="chatHistory" style="height: 180px; overflow-y: auto; margin-top: 10px; font-size: 13px; color: #ddd; line-height: 1.4;"></div>
                    <div class="chat-input">
                        <input type="text" id="chatInput" placeholder="메시지 입력..." onkeypress="if(event.key==='Enter') sendChat()">
                        <button onclick="sendChat()">전송</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const ROOM_PASSWORD = "1122";

            function checkLogin() {
                document.getElementById('loginOverlay').style.display = 'flex';
            }

            function login() {
                const inputPw = document.getElementById('pwInput').value;
                const inputNick = document.getElementById('nickInput').value.trim();
                
                if (!inputNick) {
                    alert("누군지 알 수 있게 닉네임을 적어줘 누나!");
                    return;
                }

                if (inputPw === ROOM_PASSWORD) {
                    window.myNickname = inputNick; 
                    document.getElementById('loginOverlay').style.display = 'none';
                    initCards();
                    connectWebSocket();
                } else {
                    alert("비밀번호가 틀렸어! 다시 확인해봐.");
                }
            }

            let ws = null;
            let pingInterval = null; 
            
            const cardData = Array.from({length: 8}, (_, i) => ({ id: i+1, user: `자리${i+1}`, card_bg: null }));
            const myStreams = {}; 
            const peerConnections = {}; 
            
            const rtcConfig = {
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:stun1.l.google.com:19302' }
                ]
            };

            function logChat(msg) {
                const history = document.getElementById('chatHistory');
                history.innerHTML += `<div>${msg}</div>`;
                history.scrollTop = history.scrollHeight;
            }

            function initCards() {
                const grid = document.getElementById('cardGrid');
                grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    let bgStyle = card.card_bg ? `background-image: url('${card.card_bg}');` : '';
                    grid.innerHTML += `
                        <div class="timer-card" id="card-card-${index}" style="${bgStyle}">
                            <div class="card-header">
                                <input type="text" id="username-${index}" value="${card.user}" style="flex-grow:1; min-width:0; padding:4px; font-size:12px; font-weight:bold; text-align:center; background:rgba(255,255,255,0.2); border:1px solid rgba(255,255,255,0.4); color:white; border-radius:3px;" oninput="updateUsername(${index}, this.value)">
                                
                                <div style="display:flex; gap:3px;">
                                    <button class="share-btn" id="share-btn-screen-${index}" style="background:#ff7675;" onclick="toggleShare(${index}, 'screen')">화공</button>
                                    <button class="share-btn" id="share-btn-cam-${index}" style="background:#0984e3;" onclick="toggleShare(${index}, 'cam')">캠</button>
                                </div>
                            </div>
                            
                            <div style="display:flex; justify-content:space-between; align-items:center; position:relative; z-index:3; margin-top:4px;">
                                <input type="file" id="card-file-${index}" accept="image/*" style="font-size:10px; width:100%; color:#ccc;" onchange="setCardBackground(${index}, event)">
                            </div>

                            <div class="card-stream-box" id="stream-box-${index}">
                                <span style="font-size:11px; color:#aaa; position:relative; z-index:2;">화면 미공유 중</span>
                            </div>
                        </div>
                    `;
                });
            }

            function updateUsername(index, val) {
                cardData[index].user = val;
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "username_change", index: index, user: val }));
                }
            }

            function setCardBackground(index, event) {
                const file = event.target.files[0];
                if (!file) return;

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
                        stream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: "always", frameRate: 30 }, audio: false });
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

                    box.innerHTML = `<video id="video-${index}" autoplay playsinline muted disablePictureInPicture></video>`;
                    document.getElementById(`video-${index}`).srcObject = stream;

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

                const box = document.getElementById(`stream-box-${index}`);
                const btnScreen = document.getElementById(`share-btn-screen-${index}`);
                const btnCam = document.getElementById(`share-btn-cam-${index}`);
                
                box.innerHTML = `<span style="font-size:11px; color:#aaa; position:relative; z-index:2;">화면 미공유 중</span>`;
                
                if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; }
                if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; }

                for (let key in peerConnections) {
                    if (key.startsWith(`${index}_`)) {
                        peerConnections[key].close();
                        delete peerConnections[key];
                    }
                }
            }

            function setLocalBackground(event) {
                const file = event.target.files[0];
                if (!file) return;
                
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
                        ws.send(JSON.stringify({ type: "set_nickname", nickname: myNick }));

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
                            
                            // [수정된 부분: 강퇴 버튼을 없애고 순수하게 닉네임만 예쁘게 보여줌]
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
                                logChat(`<b>${data.senderName}</b>: ${data.msg}`);
                            } 
                            else if (data.type === "init_state") {
                                const state = data.state;
                                if (state.cards) {
                                    state.cards.forEach((card, i) => {
                                        if (cardData[i]) {
                                            cardData[i].user = card.user;
                                            cardData[i].card_bg = card.card_bg;
                                            const userEl = document.getElementById(`username-${i}`);
                                            if (userEl) userEl.value = card.user;
                                            const cardEl = document.getElementById(`card-card-${i}`);
                                            if (cardEl && card.card_bg) { cardEl.style.backgroundImage = `url('${card.card_bg}')`; }
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
                                        historyEl.innerHTML += `<div><b>${chat.senderName}</b>: ${chat.msg}</div>`;
                                    });
                                    historyEl.scrollTop = historyEl.scrollHeight;
                                }
                            }
                            else if (data.type === "username_change") {
                                cardData[data.index].user = data.user;
                                const inputEl = document.getElementById(`username-${data.index}`);
                                if (inputEl) { inputEl.value = data.user; }
                            } else if (data.type === "card_bg_change") {
                                cardData[data.index].card_bg = data.dataUrl;
                                const cardEl = document.getElementById(`card-card-${data.index}`);
                                if (cardEl) { cardEl.style.backgroundImage = `url('${data.dataUrl}')`; }
                            } else if (data.type === "global_bg_image") {
                                document.getElementById('bgMediaWrapper').innerHTML = `<img src="${data.dataUrl}" alt="Full Background">`;
                            } else if (data.type === "global_bg_youtube") {
                                document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${data.videoId}?autoplay=1&mute=1&loop=1&playlist=${data.videoId}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
                            } 
                            else if (data.type === "start_share") {
                                const targetIndex = data.index;
                                const sharerId = data.sender;

                                if (data.target && data.target !== ws.clientId) return;

                                if (sharerId !== ws.clientId && ws && ws.readyState === WebSocket.OPEN) {
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

                                if (peerConnections[pcKey]) peerConnections[pcKey].close();

                                const pc = new RTCPeerConnection(rtcConfig);
                                peerConnections[pcKey] = pc;

                                pc.ontrack = (e) => {
                                    const box = document.getElementById(`stream-box-${index}`);
                                    box.innerHTML = `<video id="remote-video-${index}" autoplay playsinline muted disablePictureInPicture></video>`;
                                    document.getElementById(`remote-video-${index}`).srcObject = e.streams[0];
                                };

                                pc.onicecandidate = (e) => {
                                    if (e.candidate && ws && ws.readyState === WebSocket.OPEN) {
                                        ws.send(JSON.stringify({ type: "ice", index: index, target: senderId, candidate: e.candidate }));
                                    }
                                };

                                await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
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
                                if (pc && data.candidate) await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
                            } 
                            else if (data.type === "stop_share") {
                                const index = data.index;
                                for (let key in peerConnections) {
                                    if (key.startsWith(`${index}_`)) {
                                        peerConnections[key].close();
                                        delete peerConnections[key];
                                    }
                                }
                                const box = document.getElementById(`stream-box-${index}`);
                                box.innerHTML = `<span style="font-size:11px; color:#aaa; position:relative; z-index:2;">화면 미공유 중</span>`;
                                
                                const btnScreen = document.getElementById(`share-btn-screen-${index}`);
                                const btnCam = document.getElementById(`share-btn-cam-${index}`);
                                if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; }
                                if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; }
                            }
                            else if (data.type === "welcome") {
                                ws.clientId = data.clientId;
                                setTimeout(() => {
                                    if (ws && ws.readyState === WebSocket.OPEN) {
                                        ws.send(JSON.stringify({ type: "request_existing_shares" }));
                                    }
                                }, 300);
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
                if (peerConnections[pcKey]) peerConnections[pcKey].close();

                const pc = new RTCPeerConnection(rtcConfig);
                peerConnections[pcKey] = pc;

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

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "chat", senderName: myName, msg: msgText }));
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
    
    for idx, sharer_id in manager.active_shares.items():
        await websocket.send_text(json.dumps({"type": "start_share", "index": idx, "sender": sharer_id}))
    
    try:
        while True:
            data = await websocket.receive_text()
            packet = json.loads(data)
            p_type = packet.get("type")

            if p_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if p_type == "set_nickname":
                manager.active_users[websocket] = packet.get("nickname", "익명")
                await manager.broadcast_user_list()
                continue

            if p_type == "chat":
                chat_obj = {"senderName": packet.get("senderName"), "msg": packet.get("msg")}
                server_state["chat_history"].append(chat_obj)
                if len(server_state["chat_history"]) > 100:
                    server_state["chat_history"].pop(0)
                
                await manager.broadcast(json.dumps(packet))
                asyncio.create_task(asyncio.to_thread(save_data, server_state))
                
            else:
                packet["sender"] = client_id
                
                if p_type == "username_change":
                    server_state["cards"][packet["index"]]["user"] = packet["user"]
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
                elif p_type == "start_share":
                    manager.active_shares[packet["index"]] = client_id
                elif p_type == "stop_share":
                    idx = packet.get("index")
                    if idx in manager.active_shares:
                        del manager.active_shares[idx]
                
                await manager.broadcast(json.dumps(packet), exclude=websocket)

    except (WebSocketDisconnect, Exception):
        freed_indexes = manager.disconnect(websocket)
        await manager.broadcast_user_list()
        for idx in freed_indexes:
            await manager.broadcast(json.dumps({"type": "stop_share", "index": idx}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
