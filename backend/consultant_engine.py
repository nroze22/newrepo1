import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import logging
from collections import deque
import json

from .gemini_analyzer import GeminiAnalyzer
from .models import ConsultantMode, AnalysisResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConsultantEngine:
    """
    Real-time consulting engine that manages video analysis sessions.
    Handles frame queuing, analysis scheduling, and insight aggregation.
    """

    def __init__(self, gemini_api_key: str, frames_per_analysis: int = 30):
        self.analyzer = GeminiAnalyzer(gemini_api_key)
        self.frames_per_analysis = frames_per_analysis  # Analyze every N frames

        # Session management
        self.active_sessions: Dict[str, Dict] = {}
        self.session_analyses: Dict[str, List[Dict]] = {}
        self.frame_buffers: Dict[str, deque] = {}

        logger.info("ConsultantEngine initialized")

    def create_session(
        self,
        session_id: str,
        consultant_mode: ConsultantMode,
        custom_instructions: Optional[str] = None
    ):
        """Initialize a new consulting session."""
        self.active_sessions[session_id] = {
            'consultant_mode': consultant_mode,
            'custom_instructions': custom_instructions,
            'created_at': datetime.now(),
            'frame_count': 0,
            'analysis_count': 0,
            'last_analysis': None,
            'status': 'active'
        }
        self.session_analyses[session_id] = []
        self.frame_buffers[session_id] = deque(maxlen=self.frames_per_analysis)

        logger.info(f"Session created: {session_id} - Mode: {consultant_mode}")

    async def process_frame(
        self,
        session_id: str,
        frame_data: str,
        audio_transcript: Optional[str] = None,
        screen_share_data: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Process a video frame. Queues frames and triggers analysis
        based on the frames_per_analysis setting.
        """
        if session_id not in self.active_sessions:
            logger.error(f"Session not found: {session_id}")
            return None

        session = self.active_sessions[session_id]
        session['frame_count'] += 1

        # Add frame to buffer
        self.frame_buffers[session_id].append({
            'frame_data': frame_data,
            'audio_transcript': audio_transcript,
            'screen_share_data': screen_share_data,
            'timestamp': datetime.now()
        })

        # Trigger analysis every N frames or if we have audio/screen share
        should_analyze = (
            session['frame_count'] % self.frames_per_analysis == 0 or
            audio_transcript is not None or
            screen_share_data is not None
        )

        if should_analyze:
            return await self._perform_analysis(session_id)

        return None

    async def _perform_analysis(self, session_id: str) -> Dict[str, Any]:
        """Perform AI analysis on the current frame buffer."""
        session = self.active_sessions[session_id]
        frame_buffer = list(self.frame_buffers[session_id])

        if not frame_buffer:
            return None

        # Use the most recent frame for analysis
        latest_frame = frame_buffer[-1]

        # Aggregate audio transcripts from buffer
        audio_transcript = None
        if any(f.get('audio_transcript') for f in frame_buffer):
            transcripts = [f['audio_transcript'] for f in frame_buffer if f.get('audio_transcript')]
            audio_transcript = ' '.join(transcripts)

        # Get screen share data
        screen_share_data = latest_frame.get('screen_share_data')

        # Get previous analyses for context
        context = self.session_analyses[session_id][-5:]  # Last 5 analyses

        try:
            # Perform analysis
            analysis_result = await self.analyzer.analyze_frame(
                frame_data=latest_frame['frame_data'],
                consultant_mode=session['consultant_mode'],
                audio_transcript=audio_transcript,
                screen_share_data=screen_share_data,
                custom_instructions=session['custom_instructions'],
                context=context
            )

            # Store analysis
            analysis_result['session_id'] = session_id
            analysis_result['frame_number'] = session['frame_count']
            self.session_analyses[session_id].append(analysis_result)

            session['analysis_count'] += 1
            session['last_analysis'] = datetime.now()

            logger.info(f"Analysis completed for session {session_id} - Analysis #{session['analysis_count']}")

            return analysis_result

        except Exception as e:
            logger.error(f"Error in analysis: {str(e)}")
            return {
                'error': str(e),
                'session_id': session_id,
                'timestamp': datetime.now().isoformat()
            }

    async def generate_session_report(self, session_id: str) -> str:
        """Generate a comprehensive report for the entire session."""
        if session_id not in self.session_analyses:
            return "Session not found"

        analyses = self.session_analyses[session_id]
        session = self.active_sessions[session_id]

        report = await self.analyzer.generate_session_report(
            session_analyses=analyses,
            consultant_mode=session['consultant_mode']
        )

        return report

    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """Get a summary of session statistics and insights."""
        if session_id not in self.active_sessions:
            return {'error': 'Session not found'}

        session = self.active_sessions[session_id]
        analyses = self.session_analyses.get(session_id, [])

        # Aggregate insights
        all_insights = []
        all_recommendations = []
        all_risks = []
        all_opportunities = []

        for analysis in analyses:
            all_insights.extend(analysis.get('insights', []))
            all_recommendations.extend(analysis.get('recommendations', []))
            all_risks.extend(analysis.get('risk_factors', []))
            all_opportunities.extend(analysis.get('opportunities', []))

        # Calculate session duration
        duration = (datetime.now() - session['created_at']).total_seconds()

        return {
            'session_id': session_id,
            'consultant_mode': session['consultant_mode'],
            'duration_seconds': duration,
            'frames_processed': session['frame_count'],
            'analyses_performed': session['analysis_count'],
            'total_insights': len(all_insights),
            'total_recommendations': len(all_recommendations),
            'total_risks': len(all_risks),
            'total_opportunities': len(all_opportunities),
            'latest_insights': all_insights[-5:] if all_insights else [],
            'latest_recommendations': all_recommendations[-5:] if all_recommendations else [],
            'status': session['status']
        }

    def end_session(self, session_id: str):
        """Mark a session as completed."""
        if session_id in self.active_sessions:
            self.active_sessions[session_id]['status'] = 'completed'
            logger.info(f"Session ended: {session_id}")

    def get_active_sessions(self) -> List[str]:
        """Get list of active session IDs."""
        return [
            sid for sid, session in self.active_sessions.items()
            if session['status'] == 'active'
        ]

    async def get_instant_insight(
        self,
        frame_data: str,
        consultant_mode: str,
        question: str
    ) -> str:
        """
        Get an instant insight for a specific question about the current frame.
        Useful for interactive Q&A during sessions.
        """
        analysis = await self.analyzer.analyze_frame(
            frame_data=frame_data,
            consultant_mode=consultant_mode,
            custom_instructions=f"Please answer this specific question: {question}"
        )

        return analysis.get('detailed_analysis', 'Unable to generate insight')
