"""
Enhanced Gemini Analyzer with Production-Grade Reliability

Features:
- Retry logic with exponential backoff
- Circuit breaker pattern for API failures
- Response caching to reduce API calls
- Comprehensive error handling
- Detailed logging and metrics
- Fallback mechanisms
"""

import google.generativeai as genai
import os
import base64
from io import BytesIO
from PIL import Image
from typing import Dict, List, Any, Optional
import asyncio
from datetime import datetime, timedelta
import logging
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
from circuitbreaker import circuit
from cachetools import TTLCache
import hashlib
import json

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ]
)

logger = structlog.get_logger(__name__)


class GeminiAPIError(Exception):
    """Custom exception for Gemini API errors."""
    pass


class GeminiRateLimitError(GeminiAPIError):
    """Rate limit exceeded."""
    pass


class GeminiAnalyzer:
    """
    Production-grade AI analysis engine using Google Gemini API.

    Features:
    - Automatic retry with exponential backoff
    - Circuit breaker to prevent cascading failures
    - Response caching to reduce costs and latency
    - Comprehensive error handling and logging
    - Fallback responses for reliability
    """

    # Cache responses for 5 minutes (TTL)
    CACHE_TTL = 300
    CACHE_MAX_SIZE = 1000

    def __init__(self, api_key: str, enable_cache: bool = True):
        """
        Initialize the analyzer with reliability features.

        Args:
            api_key: Google Gemini API key
            enable_cache: Enable response caching (default: True)
        """
        try:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-1.5-pro-latest')
            self.enable_cache = enable_cache

            # Initialize cache
            if enable_cache:
                self.cache = TTLCache(maxsize=self.CACHE_MAX_SIZE, ttl=self.CACHE_TTL)

            # Metrics
            self.metrics = {
                'total_requests': 0,
                'successful_requests': 0,
                'failed_requests': 0,
                'cache_hits': 0,
                'cache_misses': 0,
                'circuit_breaker_open': 0,
                'retries': 0
            }

            logger.info("gemini_analyzer_initialized", cache_enabled=enable_cache)

        except Exception as e:
            logger.error("gemini_analyzer_init_failed", error=str(e))
            raise

        # Consultant mode prompts with enhanced instructions
        self.consultant_prompts = {
            "business": """You are a senior business strategy consultant with 20+ years experience
            at McKinsey & Company. Analyze business presentations, financial data, and strategy documents
            with expert precision. Provide insights worth $5000+/hour consulting fees. Focus on:
            - Strategic opportunities and threats with specific market data
            - Market positioning and competitive analysis with actionable insights
            - Financial implications and ROI with quantified projections
            - Risk mitigation strategies with implementation steps
            - Actionable recommendations with specific metrics and timelines

            IMPORTANT: Be specific, quantitative, and actionable. Provide concrete next steps.""",

            "design": """You are a world-class design consultant who has worked with Apple, Google,
            and top design firms. Analyze UI/UX designs, branding materials, and visual content with
            expert precision. Provide insights worth $3000+/hour. Focus on:
            - User experience and usability issues with specific improvements
            - Visual hierarchy and design principles with examples
            - Brand consistency and market appeal with competitive analysis
            - Accessibility (WCAG 2.1 AA compliance) with specific fixes
            - Specific improvement recommendations with mockup suggestions

            IMPORTANT: Reference design principles, provide before/after suggestions.""",

            "code": """You are a principal software architect with expertise across all major tech
            stacks, formerly at Google/Meta. Analyze code, architecture, and technical designs with
            expert precision. Provide insights worth $4000+/hour. Focus on:
            - Architecture patterns and anti-patterns with refactoring suggestions
            - Performance bottlenecks with profiling insights and optimization strategies
            - Security vulnerabilities (OWASP Top 10) with remediation code
            - Code quality, maintainability, technical debt with metrics
            - Specific refactoring recommendations with code examples

            IMPORTANT: Provide code snippets, reference best practices, suggest tools.""",

            "legal": """You are a senior corporate attorney with expertise in contract law, compliance,
            and risk management. Analyze legal documents and identify issues with expert precision.
            Provide insights worth $6000+/hour. Focus on:
            - Legal risks and compliance issues with severity ratings
            - Ambiguous or problematic clauses with specific language suggestions
            - Regulatory considerations with relevant statutes
            - Liability exposure with mitigation strategies
            - Specific revision recommendations with example language

            DISCLAIMER: This is for educational purposes only and not legal advice.
            IMPORTANT: Be specific about clause numbers, provide alternative language.""",

            "medical": """You are a medical imaging specialist with expertise in diagnostic analysis.
            Provide educational analysis of medical images.

            CRITICAL DISCLAIMER: This is for EDUCATIONAL PURPOSES ONLY and NOT a medical diagnosis.
            Always recommend consulting with licensed healthcare professionals.

            Focus on:
            - Observable patterns and anatomical structures (educational)
            - Educational information about potential findings
            - Questions to ask healthcare professionals
            - Recommendations to seek professional medical consultation

            IMPORTANT: Never diagnose. Always emphasize educational nature.""",

            "real_estate": """You are a real estate investment consultant with expertise in property
            analysis and market trends. Analyze property videos, listings, and market data. Provide
            insights worth $2000+/hour. Focus on:
            - Property valuation with comparable analysis and market data
            - Market trends and location analysis with growth projections
            - Renovation opportunities with cost estimates and ROI
            - Risk factors and red flags with severity assessment
            - ROI projections with detailed financial analysis

            IMPORTANT: Provide numbers, comparables, specific recommendations.""",

            "marketing": """You are a senior marketing strategist who has led campaigns for Fortune 500
            companies. Analyze marketing materials, campaigns, and strategies with expert precision.
            Provide insights worth $4000+/hour. Focus on:
            - Campaign effectiveness and messaging with A/B test suggestions
            - Target audience alignment with persona analysis
            - Competitive positioning with differentiation strategies
            - Conversion optimization opportunities with specific tactics
            - Specific tactical recommendations with expected metrics

            IMPORTANT: Provide data-driven insights, reference marketing frameworks."""
        }

    def _generate_cache_key(self, frame_data: str, consultant_mode: str,
                           custom_instructions: Optional[str]) -> str:
        """Generate a unique cache key for the request."""
        # Hash the frame data to create a shorter key
        frame_hash = hashlib.md5(frame_data[:1000].encode()).hexdigest()
        key_parts = [frame_hash, consultant_mode, custom_instructions or ""]
        return hashlib.md5("|".join(key_parts).encode()).hexdigest()

    @retry(
        retry=retry_if_exception_type((GeminiAPIError, ConnectionError, TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    @circuit(failure_threshold=5, recovery_timeout=60, expected_exception=GeminiAPIError)
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
        Analyze a video frame with comprehensive error handling and retry logic.

        Args:
            frame_data: Base64 encoded image
            consultant_mode: Type of consultant (business, design, code, etc.)
            audio_transcript: Optional audio transcription
            screen_share_data: Optional screen share content
            custom_instructions: Custom analysis instructions
            context: Previous analyses for context

        Returns:
            Structured analysis results

        Raises:
            GeminiAPIError: If API call fails after retries
        """
        self.metrics['total_requests'] += 1

        try:
            # Check cache first
            if self.enable_cache:
                cache_key = self._generate_cache_key(frame_data, consultant_mode, custom_instructions)
                if cache_key in self.cache:
                    self.metrics['cache_hits'] += 1
                    logger.info("cache_hit", consultant_mode=consultant_mode)
                    return self.cache[cache_key]
                self.metrics['cache_misses'] += 1

            # Validate inputs
            if not frame_data:
                raise ValueError("frame_data is required")
            if consultant_mode not in self.consultant_prompts:
                logger.warning("unknown_consultant_mode", mode=consultant_mode)
                consultant_mode = "business"  # Default fallback

            # Decode and validate image
            try:
                image_data = base64.b64decode(
                    frame_data.split(',')[1] if ',' in frame_data else frame_data
                )
                image = Image.open(BytesIO(image_data))

                # Validate image
                if image.size[0] < 100 or image.size[1] < 100:
                    raise ValueError("Image too small for reliable analysis")

            except Exception as e:
                logger.error("image_decode_failed", error=str(e))
                raise ValueError(f"Invalid image data: {str(e)}")

            # Build comprehensive analysis prompt
            base_prompt = self.consultant_prompts[consultant_mode]

            analysis_prompt = f"""{base_prompt}

ANALYSIS REQUEST:
Timestamp: {datetime.now().isoformat()}
Session Context: {"Ongoing session with previous context" if context else "New session"}

"""

            if audio_transcript:
                analysis_prompt += f"\nAUDIO TRANSCRIPT:\n{audio_transcript}\n"

            if screen_share_data:
                analysis_prompt += f"\nSCREEN SHARE: User is sharing their screen\n"

            if custom_instructions:
                analysis_prompt += f"\nSPECIFIC FOCUS AREAS:\n{custom_instructions}\n"

            if context and len(context) > 0:
                recent_insights = [c.get('insights', [])[:2] for c in context[-2:]]
                analysis_prompt += f"\nPREVIOUS SESSION INSIGHTS:\n{recent_insights}\n"

            analysis_prompt += """
Please provide a comprehensive expert analysis in the following structure:

1. KEY INSIGHTS (3-5 critical, specific observations)
2. RECOMMENDATIONS (3-5 actionable recommendations with concrete steps)
3. KEY FINDINGS (major discoveries or patterns with evidence)
4. RISK FACTORS (potential issues with severity: HIGH/MEDIUM/LOW)
5. OPPORTUNITIES (areas for improvement with expected impact)
6. ACTION ITEMS (specific next steps with priority and timeline)
7. CONFIDENCE SCORE (0.0-1.0 based on image quality and available data)
8. DETAILED ANALYSIS (comprehensive expert analysis with specific examples)

Requirements:
- Be specific and quantitative where possible
- Provide concrete examples and references
- Include implementation steps for recommendations
- Prioritize by impact and feasibility
- Use professional consulting language
- Provide the level of insight expected from a $5000+/hour expert
"""

            # Make API call with timeout
            logger.info("api_request_start", consultant_mode=consultant_mode)

            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.model.generate_content,
                        [analysis_prompt, image]
                    ),
                    timeout=30.0  # 30 second timeout
                )
            except asyncio.TimeoutError:
                logger.error("api_timeout", consultant_mode=consultant_mode)
                raise GeminiAPIError("API request timed out after 30 seconds")
            except Exception as e:
                error_msg = str(e).lower()
                if 'rate limit' in error_msg or 'quota' in error_msg:
                    raise GeminiRateLimitError(f"Rate limit exceeded: {str(e)}")
                raise GeminiAPIError(f"API request failed: {str(e)}")

            # Parse and validate response
            if not response or not response.text:
                raise GeminiAPIError("Empty response from API")

            analysis_text = response.text
            logger.info("api_request_success",
                       consultant_mode=consultant_mode,
                       response_length=len(analysis_text))

            # Structure the response
            result = self._parse_analysis(analysis_text)
            result['raw_analysis'] = analysis_text
            result['consultant_mode'] = consultant_mode
            result['timestamp'] = datetime.now().isoformat()
            result['cached'] = False

            # Validate result has minimum required data
            if not result.get('insights') and not result.get('detailed_analysis'):
                logger.warning("low_quality_response", consultant_mode=consultant_mode)
                result['insights'] = ["Analysis completed - see detailed analysis for full insights"]

            # Cache the result
            if self.enable_cache:
                self.cache[cache_key] = result

            self.metrics['successful_requests'] += 1
            return result

        except (GeminiAPIError, GeminiRateLimitError):
            self.metrics['failed_requests'] += 1
            raise
        except Exception as e:
            self.metrics['failed_requests'] += 1
            logger.error("analysis_failed", error=str(e), consultant_mode=consultant_mode)

            # Return fallback response
            return self._get_fallback_response(consultant_mode, str(e))

    def _parse_analysis(self, text: str) -> Dict[str, Any]:
        """
        Parse AI response into structured data with enhanced extraction.
        """
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

        lines = text.split('\n')
        current_section = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            lower_line = line.lower()

            # Section detection with better matching
            if any(keyword in lower_line for keyword in ['key insight', '## insight', '### insight']):
                current_section = 'insights'
                continue
            elif any(keyword in lower_line for keyword in ['recommendation', '## recommendation', '### recommendation']):
                current_section = 'recommendations'
                continue
            elif any(keyword in lower_line for keyword in ['finding', '## finding', 'key finding']):
                current_section = 'key_findings'
                continue
            elif any(keyword in lower_line for keyword in ['risk', '## risk', 'risk factor']):
                current_section = 'risk_factors'
                continue
            elif any(keyword in lower_line for keyword in ['opportunit', '## opportunit']):
                current_section = 'opportunities'
                continue
            elif any(keyword in lower_line for keyword in ['action', '## action', 'next step']):
                current_section = 'action_items'
                continue
            elif 'confidence' in lower_line:
                # Extract confidence score
                import re
                match = re.search(r'(\d+\.?\d*)', line)
                if match:
                    score = float(match.group(1))
                    if score > 1:
                        score = score / 100
                    result['confidence_score'] = min(max(score, 0.0), 1.0)
                continue

            # Extract list items
            if current_section and any(line.startswith(prefix) for prefix in ['-', '•', '*', '1.', '2.', '3.', '4.', '5.']):
                clean_line = line.lstrip('-•*0123456789. ')
                if len(clean_line) > 10:  # Minimum meaningful length
                    if current_section == 'action_items':
                        # Detect priority from text
                        priority = 'medium'
                        if any(word in clean_line.lower() for word in ['urgent', 'critical', 'immediate', 'asap']):
                            priority = 'high'
                        elif any(word in clean_line.lower() for word in ['later', 'eventually', 'consider']):
                            priority = 'low'

                        result[current_section].append({
                            'description': clean_line,
                            'priority': priority
                        })
                    else:
                        result[current_section].append(clean_line)

        # Ensure we have at least some insights
        if not result['insights'] and len(text) > 100:
            # Extract first few meaningful sentences as insights
            sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 50]
            result['insights'] = sentences[:3]

        return result

    def _get_fallback_response(self, consultant_mode: str, error: str) -> Dict[str, Any]:
        """
        Provide a graceful fallback response when API fails.
        """
        logger.info("using_fallback_response", consultant_mode=consultant_mode)

        return {
            'insights': [
                f"Unable to complete full analysis due to temporary service issue",
                "Please try again in a moment",
                "Your session data has been preserved"
            ],
            'recommendations': [
                "Retry the analysis",
                "Check your internet connection",
                "Contact support if issue persists"
            ],
            'key_findings': [],
            'risk_factors': [f"Technical issue: {error[:100]}"],
            'opportunities': [],
            'action_items': [
                {'description': 'Retry analysis', 'priority': 'high'}
            ],
            'confidence_score': 0.0,
            'detailed_analysis': f"Analysis temporarily unavailable. Error: {error}",
            'consultant_mode': consultant_mode,
            'timestamp': datetime.now().isoformat(),
            'is_fallback': True,
            'error': error
        }

    async def generate_session_report(
        self,
        session_analyses: List[Dict[str, Any]],
        consultant_mode: str
    ) -> str:
        """
        Generate comprehensive session report with retry logic.
        """
        try:
            # Filter out fallback responses
            valid_analyses = [a for a in session_analyses if not a.get('is_fallback')]

            if not valid_analyses:
                return "No valid analyses available for report generation."

            summary_prompt = f"""As an expert {consultant_mode} consultant, create a comprehensive
            executive summary report based on {len(valid_analyses)} analyses from this session.

Session Data:
{json.dumps(valid_analyses, indent=2)}

Create a professional consulting report that includes:

# EXECUTIVE SUMMARY
[2-3 paragraph overview of key findings]

# KEY FINDINGS AND INSIGHTS
[Consolidated insights across all analyses]

# STRATEGIC RECOMMENDATIONS
[Prioritized recommendations with implementation steps]

# RISK ASSESSMENT
[Identified risks with severity and mitigation strategies]

# OPPORTUNITY ANALYSIS
[Growth opportunities with expected ROI]

# DETAILED ACTION PLAN
[Specific action items with:
- Priority (High/Medium/Low)
- Timeline (Immediate/Short-term/Long-term)
- Resources required
- Expected outcomes]

# EXPECTED OUTCOMES AND ROI
[Quantified expected results]

Format this as a professional consulting report worth $5000+.
Use markdown formatting for clarity.
"""

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.model.generate_content,
                    summary_prompt
                ),
                timeout=45.0
            )

            return response.text

        except Exception as e:
            logger.error("report_generation_failed", error=str(e))

            # Fallback: Generate basic report from data
            return self._generate_basic_report(session_analyses, consultant_mode)

    def _generate_basic_report(self, analyses: List[Dict], mode: str) -> str:
        """Generate a basic report when AI generation fails."""
        report = f"""# {mode.upper()} CONSULTING SESSION REPORT
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Session Summary
- Total Analyses: {len(analyses)}
- Consultant Mode: {mode}

## Aggregated Insights
"""
        all_insights = []
        all_recommendations = []

        for analysis in analyses:
            all_insights.extend(analysis.get('insights', []))
            all_recommendations.extend(analysis.get('recommendations', []))

        for i, insight in enumerate(all_insights[:10], 1):
            report += f"{i}. {insight}\n"

        report += "\n## Key Recommendations\n"
        for i, rec in enumerate(all_recommendations[:10], 1):
            report += f"{i}. {rec}\n"

        return report

    def get_metrics(self) -> Dict[str, Any]:
        """Get analyzer metrics for monitoring."""
        return {
            **self.metrics,
            'cache_size': len(self.cache) if self.enable_cache else 0,
            'cache_hit_rate': (
                self.metrics['cache_hits'] / max(self.metrics['total_requests'], 1)
            ) * 100,
            'success_rate': (
                self.metrics['successful_requests'] / max(self.metrics['total_requests'], 1)
            ) * 100,
            'timestamp': datetime.now().isoformat()
        }

    def clear_cache(self):
        """Clear the response cache."""
        if self.enable_cache:
            self.cache.clear()
            logger.info("cache_cleared")
