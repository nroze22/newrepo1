from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import os
from dotenv import load_dotenv
import json
import uuid
from typing import Dict, List, Optional
import logging
from datetime import datetime

from .consultant_engine import ConsultantEngine
from .models import (
    SessionCreate, AnalysisRequest, ConsultantMode,
    WebRTCSignal, Session
)
from .social_studio import get_service as get_social_studio
from .social_studio.models import (
    BrandBrief,
    GenerateMockupsRequest,
    VoteBatch,
    BuildAssetsRequest,
    MockupUpdate,
    RegenerateImageRequest,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="AI Consultant Platform",
    description="Gemini-powered video analysis platform for expert consulting",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Consultant Engine
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY not found in environment variables")

consultant_engine = ConsultantEngine(
    gemini_api_key=GEMINI_API_KEY or "demo",
    frames_per_analysis=int(os.getenv('FRAMES_PER_ANALYSIS', 30))
)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)
        logger.info(f"WebSocket connected: {session_id}")

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            self.active_connections[session_id].remove(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
        logger.info(f"WebSocket disconnected: {session_id}")

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, message: dict, session_id: str):
        if session_id in self.active_connections:
            for connection in self.active_connections[session_id]:
                await connection.send_json(message)

manager = ConnectionManager()


# Routes

@app.get("/")
async def root():
    """Serve the main application page."""
    try:
        with open("frontend/index.html", "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>AI Consultant Platform</h1><p>Frontend files not found. Please check installation.</p>"
        )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_sessions": len(consultant_engine.get_active_sessions())
    }


@app.post("/api/sessions/create")
async def create_session(session_data: SessionCreate):
    """Create a new consulting session."""
    try:
        session_id = str(uuid.uuid4())

        consultant_engine.create_session(
            session_id=session_id,
            consultant_mode=session_data.consultant_mode,
            custom_instructions=session_data.custom_instructions
        )

        return {
            "session_id": session_id,
            "consultant_mode": session_data.consultant_mode,
            "created_at": datetime.now().isoformat(),
            "status": "active"
        }

    except Exception as e:
        logger.error(f"Error creating session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/analyze")
async def analyze_frame(session_id: str, request: AnalysisRequest):
    """Analyze a video frame with optional audio and screen share."""
    try:
        result = await consultant_engine.process_frame(
            session_id=session_id,
            frame_data=request.frame_data,
            audio_transcript=request.audio_transcript,
            screen_share_data=request.screen_share_data
        )

        if result:
            # Broadcast analysis to all connected clients
            await manager.broadcast(
                {
                    "type": "analysis",
                    "data": result
                },
                session_id
            )
            return result
        else:
            return {"status": "frame_queued", "message": "Frame queued for next analysis batch"}

    except Exception as e:
        logger.error(f"Error analyzing frame: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{session_id}/summary")
async def get_session_summary(session_id: str):
    """Get session summary with statistics and aggregated insights."""
    try:
        summary = consultant_engine.get_session_summary(session_id)
        return summary
    except Exception as e:
        logger.error(f"Error getting session summary: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/report")
async def generate_report(session_id: str):
    """Generate comprehensive consulting report for the session."""
    try:
        report = await consultant_engine.generate_session_report(session_id)
        return {
            "session_id": session_id,
            "report": report,
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/end")
async def end_session(session_id: str):
    """End a consulting session."""
    try:
        consultant_engine.end_session(session_id)
        return {
            "session_id": session_id,
            "status": "completed",
            "ended_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error ending session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/consultant-modes")
async def get_consultant_modes():
    """Get available consultant modes with descriptions."""
    return {
        "modes": [
            {
                "id": "business",
                "name": "Business Strategy",
                "description": "Senior business strategy consultant - analyze presentations, financials, and strategy",
                "pricing": "$199/session",
                "comparable_rate": "$5000+/hour consultant"
            },
            {
                "id": "design",
                "name": "Design Consultant",
                "description": "World-class design expert - UI/UX, branding, and visual design analysis",
                "pricing": "$149/session",
                "comparable_rate": "$3000+/hour consultant"
            },
            {
                "id": "code",
                "name": "Code Architect",
                "description": "Principal software architect - code review, architecture, and optimization",
                "pricing": "$179/session",
                "comparable_rate": "$4000+/hour consultant"
            },
            {
                "id": "legal",
                "name": "Legal Advisor",
                "description": "Senior corporate attorney - contract review and compliance analysis (educational)",
                "pricing": "$249/session",
                "comparable_rate": "$6000+/hour consultant"
            },
            {
                "id": "medical",
                "name": "Medical Imaging",
                "description": "Medical imaging specialist - educational analysis (not medical advice)",
                "pricing": "$99/session",
                "comparable_rate": "Educational purposes"
            },
            {
                "id": "real_estate",
                "name": "Real Estate Investor",
                "description": "Investment consultant - property analysis and market trends",
                "pricing": "$129/session",
                "comparable_rate": "$2000+/hour consultant"
            },
            {
                "id": "marketing",
                "name": "Marketing Strategist",
                "description": "Senior marketing expert - campaign analysis and strategy",
                "pricing": "$179/session",
                "comparable_rate": "$4000+/hour consultant"
            }
        ]
    }


# WebSocket endpoint for WebRTC signaling
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for WebRTC signaling and real-time communication.
    Handles video/audio connection setup and frame streaming.
    """
    await manager.connect(websocket, session_id)

    try:
        while True:
            data = await websocket.receive_json()

            message_type = data.get("type")

            if message_type == "webrtc-signal":
                # Forward WebRTC signaling messages
                await manager.broadcast(data, session_id)

            elif message_type == "frame":
                # Process video frame
                result = await consultant_engine.process_frame(
                    session_id=session_id,
                    frame_data=data.get("frame_data"),
                    audio_transcript=data.get("audio_transcript"),
                    screen_share_data=data.get("screen_share_data")
                )

                if result:
                    await manager.broadcast(
                        {
                            "type": "analysis",
                            "data": result
                        },
                        session_id
                    )

            elif message_type == "chat":
                # Handle chat messages
                await manager.broadcast(
                    {
                        "type": "chat",
                        "message": data.get("message"),
                        "timestamp": datetime.now().isoformat()
                    },
                    session_id
                )

            elif message_type == "instant-question":
                # Handle instant Q&A
                answer = await consultant_engine.get_instant_insight(
                    frame_data=data.get("frame_data"),
                    consultant_mode=data.get("consultant_mode"),
                    question=data.get("question")
                )
                await manager.send_personal_message(
                    {
                        "type": "instant-answer",
                        "answer": answer
                    },
                    websocket
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
        logger.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        manager.disconnect(websocket, session_id)


# ============================================================================
# Social Media Asset Studio
# ============================================================================

@app.get("/studio")
async def studio_page():
    """Serve the Social Media Asset Studio UI."""
    try:
        with open("frontend/studio.html", "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Social Studio</h1><p>frontend/studio.html not found.</p>",
            status_code=404,
        )


@app.post("/api/social/projects")
async def social_create_project(brief: BrandBrief):
    """Create a new studio project from a brand brief."""
    project = get_social_studio().create_project(brief)
    return {"project_id": project.id, "status": project.status}


@app.post("/api/social/projects/{project_id}/research")
async def social_research(project_id: str):
    """Run Google Search-grounded brand research via Gemini."""
    try:
        report = await get_social_studio().run_research(project_id)
        return report
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")
    except Exception as e:
        logger.exception("research failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/social/projects/{project_id}/style-guide")
async def social_style_guide(project_id: str):
    """Generate a structured brand style guide."""
    try:
        guide = await get_social_studio().run_style_guide(project_id)
        return guide
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")
    except Exception as e:
        logger.exception("style guide failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/social/projects/{project_id}/mockups")
async def social_generate_mockups(project_id: str, request: GenerateMockupsRequest):
    """Generate up to 16 image mockups using OpenAI gpt-image-1."""
    try:
        assets = await get_social_studio().generate_mockups(
            project_id,
            count=request.count,
            platforms=request.platforms,
        )
        return {"mockups": [a.model_dump(mode="json") for a in assets]}
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")
    except Exception as e:
        logger.exception("mockup generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/social/projects/{project_id}")
async def social_get_project(project_id: str):
    """Return the full project state."""
    project = get_social_studio().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project.model_dump(mode="json")


@app.get("/api/social/projects/{project_id}/mockups/{mockup_id}/image")
async def social_mockup_image(project_id: str, mockup_id: str):
    path = get_social_studio().mockup_image_path(project_id, mockup_id)
    if not path:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(str(path), media_type="image/png")


@app.post("/api/social/projects/{project_id}/vote")
async def social_vote(project_id: str, batch: VoteBatch):
    """Submit a batch of votes. Returns mockups ranked by score."""
    try:
        mockups = get_social_studio().apply_votes(project_id, batch.votes)
        return {"mockups": [m.model_dump(mode="json") for m in mockups]}
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")


@app.patch("/api/social/projects/{project_id}/mockups/{mockup_id}")
async def social_update_mockup(project_id: str, mockup_id: str, updates: MockupUpdate):
    """Patch editable fields on a mockup before building final assets."""
    try:
        asset = get_social_studio().update_mockup(
            project_id, mockup_id, updates.model_dump(exclude_none=True)
        )
        return asset.model_dump(mode="json")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/social/projects/{project_id}/mockups/{mockup_id}/regenerate")
async def social_regenerate_mockup(
    project_id: str, mockup_id: str, request: RegenerateImageRequest
):
    """Re-render a mockup's image (optionally with a new visual prompt)."""
    try:
        asset = await get_social_studio().regenerate_mockup_image(
            project_id, mockup_id, visual_prompt=request.visual_prompt
        )
        return asset.model_dump(mode="json")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("regenerate failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/social/projects/{project_id}/build")
async def social_build(project_id: str, request: BuildAssetsRequest):
    """Turn voted mockups into shippable HTML/CSS + ZIP bundles."""
    try:
        built = get_social_studio().build_assets(project_id, request.mockup_ids)
        return {
            "built": [b.model_dump(mode="json") for b in built],
            "all_download_url": f"/api/social/projects/{project_id}/built/all-assets.zip",
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("build failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/social/projects/{project_id}/built/{slug}.zip")
async def social_download_zip(project_id: str, slug: str):
    if slug == "all-assets":
        path = get_social_studio().all_assets_zip_path(project_id)
    else:
        path = get_social_studio().built_zip_path(project_id, slug)
    if not path:
        raise HTTPException(status_code=404, detail="zip not found")
    return FileResponse(
        str(path),
        media_type="application/zip",
        filename=path.name,
    )


# Serve frontend static files
try:
    app.mount("/static", StaticFiles(directory="frontend"), name="static")
except Exception as e:
    logger.warning(f"Could not mount static files: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))

    logger.info(f"Starting AI Consultant Platform on {host}:{port}")
    logger.info(f"Access the application at: http://localhost:{port}")

    uvicorn.run(app, host=host, port=port)
