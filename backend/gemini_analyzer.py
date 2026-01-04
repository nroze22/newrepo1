import google.generativeai as genai
import os
import base64
from io import BytesIO
from PIL import Image
from typing import Dict, List, Any, Optional
import asyncio
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GeminiAnalyzer:
    """
    Core AI analysis engine using Google Gemini API for multimodal analysis.
    Provides expert-level insights across different consulting domains.
    """

    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-pro-latest')

        # Consultant mode prompts - these define the expertise level
        self.consultant_prompts = {
            "business": """You are a senior business strategy consultant with 20+ years experience
            at McKinsey & Company. Analyze business presentations, financial data, and strategy documents
            with expert precision. Provide insights worth $5000+/hour consulting fees. Focus on:
            - Strategic opportunities and threats
            - Market positioning and competitive analysis
            - Financial implications and ROI
            - Risk mitigation strategies
            - Actionable recommendations with specific metrics""",

            "design": """You are a world-class design consultant who has worked with Apple, Google,
            and top design firms. Analyze UI/UX designs, branding materials, and visual content with
            expert precision. Provide insights worth $3000+/hour. Focus on:
            - User experience and usability issues
            - Visual hierarchy and design principles
            - Brand consistency and market appeal
            - Accessibility and inclusive design
            - Specific improvement recommendations with mockup suggestions""",

            "code": """You are a principal software architect with expertise across all major tech
            stacks, formerly at Google/Meta. Analyze code, architecture, and technical designs with
            expert precision. Provide insights worth $4000+/hour. Focus on:
            - Architecture patterns and anti-patterns
            - Performance bottlenecks and optimization opportunities
            - Security vulnerabilities and best practices
            - Code quality, maintainability, and technical debt
            - Specific refactoring recommendations with code examples""",

            "legal": """You are a senior corporate attorney with expertise in contract law, compliance,
            and risk management. Analyze legal documents and identify issues with expert precision.
            Provide insights worth $6000+/hour. Focus on:
            - Legal risks and compliance issues
            - Ambiguous or problematic clauses
            - Regulatory considerations
            - Liability exposure
            - Specific revision recommendations (educational purposes only)""",

            "medical": """You are a medical imaging specialist with expertise in diagnostic analysis.
            Provide educational analysis of medical images. IMPORTANT: Always include disclaimer that
            this is for educational purposes only and not a medical diagnosis. Focus on:
            - Observable patterns and anomalies
            - Educational information about conditions
            - Recommendations to consult healthcare professionals
            - Questions to ask your doctor""",

            "real_estate": """You are a real estate investment consultant with expertise in property
            analysis and market trends. Analyze property videos, listings, and market data. Provide
            insights worth $2000+/hour. Focus on:
            - Property valuation and investment potential
            - Market trends and location analysis
            - Renovation opportunities and costs
            - Risk factors and red flags
            - ROI projections and recommendations""",

            "marketing": """You are a senior marketing strategist who has led campaigns for Fortune 500
            companies. Analyze marketing materials, campaigns, and strategies with expert precision.
            Provide insights worth $4000+/hour. Focus on:
            - Campaign effectiveness and messaging
            - Target audience alignment
            - Competitive positioning
            - Conversion optimization opportunities
            - Specific tactical recommendations with metrics"""
        }

    async def analyze_frame(
        self,
        frame_data: str,
        consultant_mode: str,
        audio_transcript: Optional[str] = None,
        screen_share_data: Optional[str] = None,
        custom_instructions: Optional[str] = None,
        context: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Analyze a video frame with optional audio and screen share data.
        Returns expert-level insights based on the consultant mode.
        """
        try:
            # Decode base64 image
            image_data = base64.b64decode(frame_data.split(',')[1] if ',' in frame_data else frame_data)
            image = Image.open(BytesIO(image_data))

            # Build the analysis prompt
            base_prompt = self.consultant_prompts.get(
                consultant_mode,
                self.consultant_prompts["business"]
            )

            analysis_prompt = f"""{base_prompt}

ANALYSIS REQUEST:
Current timestamp: {datetime.now().isoformat()}

"""

            if audio_transcript:
                analysis_prompt += f"\nAUDIO TRANSCRIPT:\n{audio_transcript}\n"

            if screen_share_data:
                analysis_prompt += f"\nSCREEN SHARE CONTENT: Analyzing shared screen\n"

            if custom_instructions:
                analysis_prompt += f"\nSPECIFIC FOCUS AREAS:\n{custom_instructions}\n"

            if context:
                analysis_prompt += f"\nPREVIOUS CONTEXT: This is part of an ongoing session.\n"

            analysis_prompt += """
Please provide a comprehensive analysis in the following JSON-like structure:

1. KEY INSIGHTS (3-5 critical observations)
2. RECOMMENDATIONS (3-5 specific, actionable recommendations)
3. KEY FINDINGS (major discoveries or patterns)
4. RISK FACTORS (potential issues or concerns)
5. OPPORTUNITIES (areas for improvement or growth)
6. ACTION ITEMS (specific next steps with priority)
7. CONFIDENCE SCORE (0.0-1.0 based on image quality and clarity)
8. DETAILED ANALYSIS (comprehensive expert analysis)

Be specific, quantitative where possible, and provide the level of insight
expected from a $5000+/hour expert consultant.
"""

            # Generate analysis
            response = await asyncio.to_thread(
                self.model.generate_content,
                [analysis_prompt, image]
            )

            # Parse response
            analysis_text = response.text

            # Structure the response
            result = self._parse_analysis(analysis_text)
            result['raw_analysis'] = analysis_text
            result['consultant_mode'] = consultant_mode
            result['timestamp'] = datetime.now().isoformat()

            logger.info(f"Analysis completed for mode: {consultant_mode}")
            return result

        except Exception as e:
            logger.error(f"Error in analysis: {str(e)}")
            return {
                'error': str(e),
                'insights': ['Error processing frame - please try again'],
                'recommendations': [],
                'key_findings': [],
                'confidence_score': 0.0
            }

    def _parse_analysis(self, text: str) -> Dict[str, Any]:
        """Parse the AI response into structured data."""
        # This is a simple parser - you might want to use more sophisticated parsing
        result = {
            'insights': [],
            'recommendations': [],
            'key_findings': [],
            'risk_factors': [],
            'opportunities': [],
            'action_items': [],
            'confidence_score': 0.85,
            'detailed_analysis': text
        }

        # Extract sections (simple implementation)
        lines = text.split('\n')
        current_section = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            lower_line = line.lower()
            if 'key insight' in lower_line or 'insight' in lower_line:
                current_section = 'insights'
            elif 'recommendation' in lower_line:
                current_section = 'recommendations'
            elif 'finding' in lower_line:
                current_section = 'key_findings'
            elif 'risk' in lower_line:
                current_section = 'risk_factors'
            elif 'opportunit' in lower_line:
                current_section = 'opportunities'
            elif 'action' in lower_line:
                current_section = 'action_items'
            elif 'confidence' in lower_line:
                # Try to extract confidence score
                import re
                match = re.search(r'(\d+\.?\d*)', line)
                if match:
                    score = float(match.group(1))
                    if score > 1:
                        score = score / 100
                    result['confidence_score'] = min(score, 1.0)
            elif current_section and line.startswith(('-', '•', '*', str)):
                # This is a list item
                clean_line = line.lstrip('-•*0123456789. ')
                if clean_line:
                    if current_section == 'action_items':
                        result[current_section].append({
                            'description': clean_line,
                            'priority': 'high' if any(word in clean_line.lower() for word in ['urgent', 'critical', 'immediate']) else 'medium'
                        })
                    else:
                        result[current_section].append(clean_line)

        return result

    async def generate_session_report(
        self,
        session_analyses: List[Dict[str, Any]],
        consultant_mode: str
    ) -> str:
        """Generate a comprehensive session report from all analyses."""
        try:
            summary_prompt = f"""As an expert {consultant_mode} consultant, create a comprehensive
            executive summary report based on the following session analyses:

{session_analyses}

Create a professional consulting report that includes:
1. Executive Summary
2. Key Findings and Insights
3. Strategic Recommendations
4. Risk Assessment
5. Opportunity Analysis
6. Detailed Action Plan with Timeline
7. Expected Outcomes and ROI

Format this as a professional consulting report worth $5000+.
"""

            response = await asyncio.to_thread(
                self.model.generate_content,
                summary_prompt
            )

            return response.text

        except Exception as e:
            logger.error(f"Error generating report: {str(e)}")
            return f"Error generating report: {str(e)}"
