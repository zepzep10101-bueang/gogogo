from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from typing import List
import json
import uvicorn

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

@app.get("/", response_class=HTMLResponse)
def read_root():
    html_content = """
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>행운방 대시보드</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Arial', sans-serif; }
            body, html { width: 100%; height: 100%; overflow-x: hidden; overflow-y: auto; }

            .video-background {
                position: fixed;
                top: 0; left: 0; width: 100vw; height: 100vh;
                z-index: -2;
                overflow: hidden;
                pointer-events: none;
            }
            .video-background iframe {
                position: absolute;
                top: 50%; left: 50%;
                width: 100vw; height: 56.25vw;
                min-height: 100vh; min-width: 177.77vh;
                transform: translate(-50%, -50%);
                pointer-events: none;
            }

            .overlay {
                position: fixed;
                top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(0, 0, 0, 0.4);
                z-index: -1;
                pointer-events: none;
            }

            .main-container {
                display: grid;
                grid-template-columns: 3fr 1fr;
                gap: 20px;
                padding: 20px;
                min-height: 100vh;
                color: white;
                position: relative;
                z-index: 1;
            }

            .card-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
                gap: 15px;
                align-content: start;
            }

            .timer-card {
                background: rgba(20, 20, 30, 0.85);
                border-radius: 12px;
                padding: 12px;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
                border: 1px solid rgba(255, 255, 255, 0.25);
                backdrop-filter: blur(5px);
                min-height: 330px;
                position: relative;
                overflow: hidden;
            }

            .card-media-bg {
                position: absolute;
                top: 0; left: 0; width: 100%; height: 100%;
                z-index: 1;
                opacity: 0.4;
                pointer-events: none;
                overflow: hidden;
            }
            .card-media-bg img {
                width: 100%;
                height: 100%;
                object-fit: cover;
                position: absolute;
                top: 0; left: 0;
            }

            .card-stream-box {
                width: 100%;
                height: 160px;
                background: rgba(0, 0, 0, 0.7);
                border-radius: 6px;
                overflow: hidden;
                position: relative;
                margin-top: 6px;
                display: flex;
                align-items: center;
                justify-content: center;
                border: 1px solid rgba(255,255,255,0.2);
                z-index: 2;
            }

            .card-stream-box video {
                width: 100%;
                height: 100%;
                object-fit: fill;
                position: relative;
                z-index: 2;
                background: black;
            }

            .card-memo {
                background: rgba(0, 0, 0, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.3);
                color: white;
                padding: 6px;
                border-radius: 4px;
                font-size: 11px;
                resize: none;
                height: 50px;
                margin-top: 6px;
                position: relative;
                z-index: 2;
            }

            .btn-action {
                background: #0984e3;
                color: white;
                border: none;
                padding: 3px 6px;
                font-size: 10px;
                border-radius: 4px;
                cursor: pointer;
                transition: 0.2s;
                z-index: 2;
                position: relative;
            }
            .btn-action:hover { background: #74b9ff; }

            .side-panel {
                display: flex;
                flex-direction: column;
                gap: 15px;
            }
            .panel-box {
                background: rgba(30, 30, 40, 0.85);
                border-radius: 12px;
                padding: 15px;
                border: 1px solid rgba(255, 255, 255, 0.2);
                backdrop-filter: blur(5px);
            }
            .chat-box {
                flex-grow: 1;
                display: flex;
                flex-direction: column;
                justify-content: flex-end;
            }
            .chat-input {
                display: flex;
                margin-top: 10px;
            }
            .chat-input input {
                flex-grow: 1;
                padding: 8px;
                border-radius: 4px;
                border: none;
                background: rgba(255, 255, 255, 0.9);
                color: black;
            }
            .chat-input button {
                padding: 8px 15px;
                background: #ff7675;
                border: none;
                color: white;
                border-radius: 4px;
                cursor: pointer;
                margin-left: 5px;
            }
            
            .bg-control {
                display: flex;
                gap: 5px;
                margin-top: 8px;
            }
            .bg-control input {
                flex-grow: 1;
                padding: 6px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.1);
                color: white;
                font-size: 12px;
            }
            .bg-control button {
                padding: 6px 12px;
                background: #0984e3;
                border: none;
                color: white;
                border-radius: 4px;
                cursor: pointer;
                font-size: 12px;
            }
        </style>
    </head>
    <body>

        <div class="video-background" id="bgContainer">
            <iframe id="ytVideo" src="https://www.youtube.com/embed/jfKfPfyJRdk?autoplay=1&mute=1&loop=1&playlist=jfKfPfyJRdk&controls=0&showinfo=0" 
                    frameborder="0" allow="autoplay; encrypted-media"></iframe>
        </div>
        <div class="overlay"></div>

        <div class="main-container">
            <div class="card-grid" id="cardGrid"></div>

            <div class="side-panel">
                <div class="panel-box" id="masterPanel" style="display:none;">
                    <h3>🖼️ [방장전용] 메인 유튜브 배경 변경</h3>
                    <div class="bg-control">
                        <input type="text" id="ytInput" placeholder="유튜브 주소/ID 입력">
                        <button onclick="changeMasterBackground()">변경</button>
                    </div>
                </div>

                <div class="panel-box">
                    <h3>👑 대시보드</h3>
                    <p style="margin-top:5px; font-size:14px;">현재 접속 인원: <span id="userCount" style="color:#ff7675; font-weight:bold;">1명</span> / 최대 10명</p>
                </div>

                <div class="panel-box chat-box">
                    <h3>💬 실시간 채팅</h3>
                    <div id="chatHistory" style="height: 180px; overflow-y: auto; margin-top: 10px; font-size: 13px; color: #ddd; line-height: 1.4;">
                        [안내] 행운방 대시보드에 입장했습니다.
                    </div>
                    <div class="chat-input">
                        <input type="text" id="chatInput" placeholder="메시지를 입력하세요..." onkeypress="if(event.key==='Enter') sendChat()">
                        <button onclick="sendChat()">전송</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let isHost = false;
            let localStream = null;

            const cardData = [
                { id: 1, user: '누나1', memo: '' },
                { id: 2, user: '누나2', memo: '' },
                { id: 3, user: '누나3', memo: '' },
                { id: 4, user: '누나4', memo: '' },
                { id: 5, user: '누나5', memo: '' },
                { id: 6, user: '누나6', memo: '' },
                { id: 7, user: '누나7', memo: '' },
                { id: 8, user: '누나8', memo: '' }
            ];

            function initCards() {
                const grid = document.getElementById('cardGrid');
                grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    const cardHtml = `
                        <div class="timer-card" id="card-${index}">
                            <div class="card-media-bg" id="card-media-${index}">
                                <img id="img-${index}" src="" style="display:none;" alt="BG">
                            </div>

                            <div style="display:flex; justify-content:space-between; align-items:center; gap:4px; position:relative; z-index:2;">
                                <input type="text" value="${card.user}" placeholder="아이디" style="width:75px; padding:2px; font-size:11px; background:rgba(255,255,255,0.2); border:1px solid rgba(255,255,255,0.4); color:white; border-radius:3px;" oninput="cardData[${index}].user = this.value">
                                <input type="file" accept="image/*,image/gif" style="width:75px; font-size:9px; padding:1px;" onchange="loadCardImage(event, ${index})">
                                <button class="btn-action" onclick="toggleScreenShare(${index})">🖥️ 화면공유</button>
                            </div>
                            
                            <div class="card-stream-box">
                                <video id="video-${index}" autoplay playsinline muted></video>
                                <span id="placeholder-${index}" style="position:absolute; font-size:11px; color:#aaa; z-index:1;">화면 미공유 중</span>
                            </div>

                            <div style="position:relative; z-index:2;">
                                <textarea class="card-memo" placeholder="메모 입력란..." oninput="cardData[${index}].memo = this.value">${card.memo}</textarea>
                            </div>
                        </div>
                    `;
                    grid.innerHTML += cardHtml;
                });
            }

            function loadCardImage(event, index) {
                const file = event.target.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = function(e) {
                    const imgEl = document.getElementById(`img-${index}`);
                    imgEl.src = e.target.result;
                    imgEl.style.display = "block";
                };
                reader.readAsDataURL(file);
            }

            async function toggleScreenShare(index) {
                const videoEl = document.getElementById(`video-${index}`);
                const placeholderEl = document.getElementById(`placeholder-${index}`);

                try {
                    if (!localStream) {
                        localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
                        videoEl.srcObject = localStream;
                        placeholderEl.style.display = 'none';

                        localStream.getVideoTracks()[0].onended = () => {
                            stopScreenShare(index);
                        };
                    } else {
                        stopScreenShare(index);
                    }
                } catch (err) {
                    console.log("화면 공유 취소 또는 오류:", err);
                }
            }

            function stopScreenShare(index) {
                if (localStream) {
                    localStream.getTracks().forEach(track => track.stop());
                    localStream = null;
                }
                const videoEl = document.getElementById(`video-${index}`);
                const placeholderEl = document.getElementById(`placeholder-${index}`);
                videoEl.srcObject = null;
                placeholderEl.style.display = 'block';
            }

            initCards();

            const ws = new WebSocket("ws://" + window.location.host + "/ws");
            
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                if (data.type === "chat") {
                    const history = document.getElementById('chatHistory');
                    history.innerHTML += `<br>${data.msg}`;
                    history.scrollTop = history.scrollHeight;
                } else if (data.type === "count") {
                    document.getElementById('userCount').innerText = data.count + "명";
                } else if (data.type === "set_host") {
                    isHost = true;
                    document.getElementById('masterPanel').style.display = "block";
                    const history = document.getElementById('chatHistory');
                    history.innerHTML += `<br><span style="color:#ff7675;">[안내] 누나는 이 방의 방장(Host)입니다. 메인 유튜브 배경을 변경할 수 있습니다.</span>`;
                    history.scrollTop = history.scrollHeight;
                } else if (data.type === "bg_change") {
                    const iframe = document.getElementById('ytVideo');
                    iframe.src = `https://www.youtube.com/embed/${data.videoId}?autoplay=1&mute=1&loop=1&playlist=${data.videoId}&controls=0&showinfo=0`;
                }
            };

            function sendChat() {
                const input = document.getElementById('chatInput');
                if (!input.value) return;
                ws.send(JSON.stringify({ type: "chat", msg: input.value }));
                input.value = '';
            }

            function changeMasterBackground() {
                if (!isHost) return;
                const inputVal = document.getElementById('ytInput').value.trim();
                if (!inputVal) return;
                let videoId = inputVal;
                if (inputVal.includes('youtu.be/')) {
                    videoId = inputVal.split('youtu.be/')[1].split('?')[0];
                } else if (inputVal.includes('watch?v=')) {
                    videoId = inputVal.split('watch?v=').pop().split('&')[0];
                }
                ws.send(JSON.stringify({ type: "bg_change", videoId: videoId }));
            }
        </script>
    </body>
    </html>
    """
    return html_content

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if len(manager.active_connections) >= 10:
        await websocket.accept()
        await websocket.send_text(json.dumps({"type": "chat", "msg": "[안내] 인원이 가득 찼습니다. (최대 10명)"}))
        await websocket.close()
        return

    await manager.connect(websocket)
    
    is_host = (len(manager.active_connections) == 1)
    if is_host:
        await websocket.send_text(json.dumps({"type": "set_host"}))

    await manager.broadcast(json.dumps({"type": "count", "count": len(manager.active_connections)}))
    
    try:
        while True:
            data = await websocket.receive_text()
            packet = json.loads(data)
            if packet.get("type") == "chat":
                await manager.broadcast(json.dumps({"type": "chat", "msg": f"상대방: {packet.get('msg')}"}))
            elif packet.get("type") == "bg_change":
                await manager.broadcast(json.dumps({"type": "bg_change", "videoId": packet.get("videoId")}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast(json.dumps({"type": "count", "count": len(manager.active_connections)}))
        await manager.broadcast(json.dumps({"type": "chat", "msg": "[안내] 누군가 나갔습니다."}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main_new:app", host="0.0.0.0", port=port)