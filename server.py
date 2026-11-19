import os
import json
import base64
import tempfile
from typing import Dict, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from groq import Groq
import edge_tts

load_dotenv()

app = FastAPI(title="Community Audio Hub - Multi-Room")
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

ROOMS: Dict[str, dict] = {
    "global-solutions": {
        "title": "Global Solutions & Peace",
        "persona": "You are an inspiring global moderator guiding constructive dialogue on peace, sustainability, and shared human progress. Keep replies under 2 sentences."
    },
    "oratory-leadership": {
        "title": "Oratory & Leadership",
        "persona": "You are an encouraging speech and leadership coach helping guests refine their voice, confidence, and storytelling. Keep replies under 2 sentences."
    },
    "wellness-mindfulness": {
        "title": "Wellness & Mindful Living",
        "persona": "You are a serene wellness host fostering emotional balance, gratitude, and mental clarity. Keep replies under 2 sentences."
    },
    "creative-arts": {
        "title": "Creative Arts & Music",
        "persona": "You are an expressive arts and music host celebrating poetry, song, rhythm, and human creativity. Keep replies under 2 sentences."
    },
    "innovation-economy": {
        "title": "Innovation & Future Economy",
        "persona": "You are a forward-thinking innovation host discussing sustainable livelihoods, technology, and community enterprise. Keep replies under 2 sentences."
    }
}

class RoomManager:
    def __init__(self):
        self.active_rooms: Dict[str, List[dict]] = {r: [] for r in ROOMS}
        self.room_history: Dict[str, List[dict]] = {r: [] for r in ROOMS}

    async def join(self, room_id: str, websocket: WebSocket, username: str):
        if room_id not in self.active_rooms:
            room_id = "global-solutions"
        self.active_rooms[room_id].append({"ws": websocket, "name": username})
        await self.broadcast_roster(room_id)

    async def leave(self, room_id: str, websocket: WebSocket):
        if room_id in self.active_rooms:
            self.active_rooms[room_id] = [
                m for m in self.active_rooms[room_id] if m["ws"] != websocket
            ]
            await self.broadcast_roster(room_id)

    async def broadcast_roster(self, room_id: str):
        members = [m["name"] for m in self.active_rooms.get(room_id, [])]
        payload = json.dumps({
            "type": "roster",
            "room_id": room_id,
            "room_title": ROOMS[room_id]["title"],
            "members": members
        })
        await self.broadcast_json(room_id, payload)

    async def broadcast_json(self, room_id: str, message_str: str):
        disconnected = []
        for member in self.active_rooms.get(room_id, []):
            try:
                await member["ws"].send_text(message_str)
            except Exception:
                disconnected.append(member["ws"])
        for ws in disconnected:
            await self.leave(room_id, ws)

manager = RoomManager()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_frontend():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return JSONResponse({"status": "awake", "service": "Community Audio Hub"})

@app.websocket("/ws/audio/{room_id}")
async def audio_websocket(websocket: WebSocket, room_id: str, name: str = "Guest"):
    await websocket.accept()
    if room_id not in ROOMS:
        room_id = "global-solutions"

    clean_name = (name or "Guest").strip()[:20] or "Guest"
    await manager.join(room_id, websocket, clean_name)

    try:
        while True:
            audio_bytes = await websocket.receive_bytes()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                with open(tmp_path, "rb") as audio_file:
                    transcript = groq_client.audio.transcriptions.create(
                        file=(tmp_path, audio_file.read()),
                        model="whisper-large-v3-turbo",
                    )
                user_text = transcript.text.strip()
                if not user_text:
                    user_text = "(Unrecognized audio)"

                user_audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

                room_persona = ROOMS[room_id]["persona"]
                system_prompt = (
                    f"{room_persona} "
                    "Address the speaker warmly by name when natural. "
                    "Also pick a soundscape mood from: 'Calm Acoustic', 'Upbeat Lo-Fi', 'Warm Ambient'. "
                    "Return valid JSON with keys: 'reply' and 'music_mood'."
                )

                history = manager.room_history[room_id]
                history.append({"role": "user", "content": f"{clean_name} says: {user_text}"})
                recent_messages = [{"role": "system", "content": system_prompt}] + history[-8:]

                chat_res = groq_client.chat.completions.create(
                    model="openai/gpt-oss-20b",
                    response_format={"type": "json_object"},
                    messages=recent_messages
                )
                ai_data = json.loads(chat_res.choices.message.content)
                reply_text = ai_data.get("reply", f"Thank you for sharing that insight, {clean_name}.")
                music_mood = ai_data.get("music_mood", "Calm Acoustic")

                history.append({"role": "assistant", "content": reply_text})

                mp3_path = tmp_path + ".mp3"
                communicate = edge_tts.Communicate(reply_text, "en-US-AndrewNeural")
                await communicate.save(mp3_path)

                with open(mp3_path, "rb") as f:
                    ai_audio_b64 = base64.b64encode(f.read()).decode("utf-8")

                if os.path.exists(mp3_path):
                    os.remove(mp3_path)

                await manager.broadcast_json(room_id, json.dumps({
                    "type": "turn",
                    "speaker": clean_name,
                    "user_text": user_text,
                    "user_audio_base64": user_audio_b64,
                    "reply": reply_text,
                    "music_mood": music_mood,
                    "ai_audio_base64": ai_audio_b64
                }))
            except Exception as e:
                await websocket.send_text(json.dumps({"type": "error", "error": str(e)}))
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    except WebSocketDisconnect:
        await manager.leave(room_id, websocket)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
