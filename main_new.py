from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import json
import uvicorn
import os

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str, exclude: WebSocket = None):
        for connection in list(self.active_connections):
            if connection != exclude:
                try:
                    await connection.send_text(message)
                except Exception:
                    self.disconnect(connection)

manager = ConnectionManager()

@app.get("/", response_class=HTMLResponse)
def read_root():
    return r"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>행운방 대시보드</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Arial', sans-serif; }
            body, html { width: 100%; height: 100%; overflow-x: hidden; overflow-y: auto; background: #111; }

            .video-background {
                position: fixed;
                top: 0; left: 0; 
                width: 100vw; height: 100vh;
                z-index: 0;
                overflow: hidden;
                pointer-events: none;
                background: #000;
            }
            
            #bgMediaWrapper {
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
            }

            #bgMediaWrapper img {
                width: 100vw;
                height: 100vh;
                object-fit: cover;
                display: block;
            }

            #bgMediaWrapper iframe {
                width: 100vw;
                height: 100vh;
                pointer-events: none;
                border: none;
            }

            .overlay {
                position: fixed;
                top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(0, 0, 0, 0.35);
                z-index: 1;
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
                z-index: 2;
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
                min-height: 350px;
                position: relative;
                overflow: hidden;
            }

            .card-media-bg {
                position: absolute;
                top: 0; left: 0; width: 100%; height: 100%;
                z-index: 1;
                opacity: 0.45;
                pointer-events: none;
                overflow: hidden;
            }
            .card-media-bg img {
                width: 100%;
                height: 100%;
                object-fit: cover;
            }

            .card-stream-box {
                width: 100%;
                height: 170px;
                background: rgba(0, 0, 0, 0.7);
                border-radius: 6px;
                overflow: hidden;
                position: relative;
                margin-top: 6px;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                border: 1px solid rgba(255,255,255,0.2);
                z-index: 2;
            }

            .card-stream-box video {
                width: 100%;
                height: 100%;
                object-fit: contain;
                background: #000;
            }

            .share-btn {
                padding: 5px 10px;
                font-size: 11px;
                background: #ff7675;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                margin-top: 5px;
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
                width: 100%;
            }

            .side-panel { display: flex; flex-direction: column; gap: 15px; }
            .panel-box {
                background: rgba(30, 30, 40, 0.85);
                border-radius: 12px;
                padding: 15px;
                border: 1px solid rgba(255, 255, 255, 0.2);
                backdrop-filter: blur(5px);
            }
            .chat-box { flex-grow: 1; display: flex; flex-direction: column; justify-content: flex-end; }
            .chat-input { display: flex; margin-top: 10px; }
            .chat-input input {
                flex-grow: 1; padding: 8px; border-radius: 4px; border: none;
                background: rgba(255, 255, 255, 0.9); color: black;
            }
            .chat-input button {
                padding: 8px 15px; background: #ff7675; border: none;
                color: white; border-radius: 4px; cursor: pointer; margin-left: 5px;
            }
            
            .bg-control { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
            .status-indicator {
                font-size: 11px; padding: 2px 6px; border-radius: 3px; display: inline-block; margin-left: 5px;
            }
            .status-online { background: #00b894; color: white; }
            .status-offline { background: #d63031; color: white; }
        </style>
    </head>
    <body>

        <div class="video-background" id="bgContainer">
            <div id="bgMediaWrapper"></div>
        </div>
        <div class="overlay"></div>

        <div class="main-container">
            <div class="card-grid" id="cardGrid"></div>

            <div class="side-panel">
                <div class="panel-box">
                    <h3>👑 대시보드 <span id="connStatus" class="status-indicator status-offline">연결 중...</span></h3>
                    <p style="margin-top:5px; font-size:14px;">현재 접속 인원: <span id="userCount" style="color:#ff7675; font-weight:bold;">0명</span></p>
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
            let ws = null;
            const cardData = Array.from({length: 8}, (_, i) => ({ id: i+1, user: `누나${i+1}`, memo: '' }));
            let localStream = null;
            let mySharingIndex = null;
            const peerConnections = {};
            const rtcConfig = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

            function logChat(msg) {
                const history = document.getElementById('chatHistory');
                history.innerHTML += `<div>${msg}</div>`;
                history.scrollTop = history.scrollHeight;
            }

            function initCards() {
                const grid = document.getElementById('cardGrid');
                grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    grid.innerHTML += `
                        <div class="timer-card">
                            <div class="card-media-bg" id="card-media-${index}"></div>
                            <div style="display:flex; justify-content:space-between; align-items:center; gap:4px; position:relative; z-index:2;">
                                <input type="text" value="${card.user}" style="width:75px; padding:2px; font-size:11px; background:rgba(255,255,255,0.2); border:1px solid rgba(255,255,255,0.4); color:white; border-radius:3px;">
                                <input type="file" accept="image/*" style="width:75px; font-size:9px; padding:1px;" onchange="loadCardImage(event, ${index})">
                            </div>
                            
                            <div class="card-stream-box" id="stream-box-${index}">
                                <span style="font-size:11px; color:#aaa; margin-bottom: 5px;">화면 미공유 중</span>
                                <button class="share-btn" onclick="toggleScreenShare(${index})">🖥️ 화면 공유</button>
                            </div>

                            <div style="position:relative; z-index:2;">
                                <textarea class="card-memo" placeholder="메모 입력란..."></textarea>
                            </div>
                        </div>
                    `;
                });
            }

            async function toggleScreenShare(index) {
                const box = document.getElementById(`stream-box-${index}`);
                
                if (mySharingIndex === index) {
                    stopMyShare();
                    return;
                }

                if (mySharingIndex !== null) {
                    stopMyShare();
                }

                try {
                    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
                    localStream = stream;
                    mySharingIndex = index;

                    box.innerHTML = `<video id="video-${index}" autoplay playsinline muted></video>`;
                    document.getElementById(`video-${index}`).srcObject = stream;

                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "start_share", index: index }));
                    }

                    stream.getVideoTracks()[0].onended = () => {
                        stopMyShare();
                    };
                } catch (err) {
                    console.error("화면 공유 에러:", err);
                }
            }

            function stopMyShare() {
                if (localStream) {
                    localStream.getTracks().forEach(track => track.stop());
                    localStream = null;
                }
                if (mySharingIndex !== null) {
                    const idx = mySharingIndex;
                    mySharingIndex = null;

                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "stop_share", index: idx }));
                    }

                    const box = document.getElementById(`stream-box-${idx}`);
                    box.innerHTML = `
                        <span style="font-size:11px; color:#aaa; margin-bottom: 5px;">화면 미공유 중</span>
                        <button class="share-btn" onclick="toggleScreenShare(${idx})">🖥️ 화면 공유</button>
                    `;
                }
            }

            function loadCardImage(event, index) {
                const file = event.target.files[0];
                if (!file) return;

                const img = new Image();
                const blobUrl = URL.createObjectURL(file);
                img.onload = function() {
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    canvas.width = 300; canvas.height = 300;
                    ctx.drawImage(img, 0, 0, 300, 300);
                    const compressedUrl = canvas.toDataURL('image/jpeg', 0.6);

                    document.getElementById(`card-media-${index}`).innerHTML = `<img src="${compressedUrl}" alt="BG">`;

                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ type: "card_bg_change", index: index, imgUrl: compressedUrl }));
                    }
                };
                img.src = blobUrl;
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

                if (!videoId) {
                    alert("유튜브 링크가 올바르지 않습니다.");
                    return;
                }

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
                    };

                    ws.onmessage = async function(event) {
                        try {
                            const data = JSON.parse(event.data);
                            if (data.type === "chat") {
                                logChat(data.msg);
                            } else if (data.type === "count") {
                                document.getElementById('userCount').innerText = data.count + "명";
                            } else if (data.type === "card_bg_change") {
                                const targetBg = document.getElementById(`card-media-${data.index}`);
                                if (targetBg) {
                                    targetBg.innerHTML = `<img src="${data.imgUrl}" alt="BG">`;
                                }
                            } else if (data.type === "global_bg_image") {
                                document.getElementById('bgMediaWrapper').innerHTML = `<img src="${data.dataUrl}" alt="Full Background">`;
                            } else if (data.type === "global_bg_youtube") {
                                document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${data.videoId}?autoplay=1&mute=1&loop=1&playlist=${data.videoId}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
                            } 
                            else if (data.type === "request_peer_offer") {
                                const targetIndex = data.index;
                                if (mySharingIndex === targetIndex && localStream) {
                                    const pc = new RTCPeerConnection(rtcConfig);
                                    peerConnections[targetIndex] = pc;

                                    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));

                                    pc.onicecandidate = (e) => {
                                        if (e.candidate && ws && ws.readyState === WebSocket.OPEN) {
                                            ws.send(JSON.stringify({ type: "ice", index: targetIndex, candidate: e.candidate }));
                                        }
                                    };

                                    const offer = await pc.createOffer();
                                    await pc.setLocalDescription(offer);

                                    if (ws && ws.readyState === WebSocket.OPEN) {
                                        ws.send(JSON.stringify({ type: "offer", index: targetIndex, sdp: pc.localDescription }));
                                    }
                                }
                            }
                            else if (data.type === "offer") {
                                const index = data.index;
                                const pc = new RTCPeerConnection(rtcConfig);
                                peerConnections[index] = pc;

                                pc.ontrack = (e) => {
                                    const box = document.getElementById(`stream-box-${index}`);
                                    box.innerHTML = `<video id="remote-video-${index}" autoplay playsinline></video>`;
                                    document.getElementById(`remote-video-${index}`).srcObject = e.streams[0];
                                };

                                pc.onicecandidate = (e) => {
                                    if (e.candidate && ws && ws.readyState === WebSocket.OPEN) {
                                        ws.send(JSON.stringify({ type: "ice", index: index, candidate: e.candidate }));
                                    }
                                };

                                await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
                                const answer = await pc.createAnswer();
                                await pc.setLocalDescription(answer);

                                if (ws && ws.readyState === WebSocket.OPEN) {
                                    ws.send(JSON.stringify({ type: "answer", index: index, sdp: pc.localDescription }));
                                }
                            } else if (data.type === "answer") {
                                const pc = peerConnections[data.index];
                                if (pc) {
                                    await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
                                }
                            } else if (data.type === "ice") {
                                const pc = peerConnections[data.index];
                                if (pc && data.candidate) {
                                    await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
                                }
                            } else if (data.type === "stop_share") {
                                const index = data.index;
                                if (peerConnections[index]) {
                                    peerConnections[index].close();
                                    delete peerConnections[index];
                                }
                                const box = document.getElementById(`stream-box-${index}`);
                                box.innerHTML = `
                                    <span style="font-size:11px; color:#aaa; margin-bottom: 5px;">화면 미공유 중</span>
                                    <button class="share-btn" onclick="toggleScreenShare(${index})">🖥️ 화면 공유</button>
                                `;
                            }
                        } catch(e) {
                            console.error("데이터 처리 에러:", e);
                        }
                    };

                    ws.onclose = function() {
                        const statusEl = document.getElementById('connStatus');
                        statusEl.innerText = "연결 끊김";
                        statusEl.className = "status-indicator status-offline";
                        setTimeout(connectWebSocket, 2000);
                    };
                } catch(e) {
                    setTimeout(connectWebSocket, 2000);
                }
            }

            function sendChat() {
                const input = document.getElementById('chatInput');
                if (!input.value.trim()) return;
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "chat", msg: input.value }));
                    input.value = '';
                }
            }

            initCards();
            connectWebSocket();
        </script>
    </body>
    </html>
    """

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    await manager.broadcast(json.dumps({"type": "count", "count": len(manager.active_connections)}))
    
    try:
        while True:
            data = await websocket.receive_text()
            packet = json.loads(data)
            p_type = packet.get("type")

            if p_type == "chat":
                msg_text = packet.get('msg')
                for conn in manager.active_connections:
                    if conn == websocket:
                        await conn.send_text(json.dumps({"type": "chat", "msg": f"나: {msg_text}"}))
                    else:
                        await conn.send_text(json.dumps({"type": "chat", "msg": f"상대방: {msg_text}"}))
            elif p_type == "start_share":
                idx = packet.get("index")
                await manager.broadcast(json.dumps({"type": "request_peer_offer", "index": idx}), exclude=websocket)
            elif p_type in ["card_bg_change", "global_bg_image", "global_bg_youtube", "offer", "answer", "ice", "stop_share"]:
                await manager.broadcast(json.dumps(packet), exclude=websocket)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast(json.dumps({"type": "count", "count": len(manager.active_connections)}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
