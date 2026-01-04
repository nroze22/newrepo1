"""
Tests for Gemini Analyzer

Comprehensive test suite for the AI analysis engine.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, MagicMock
from backend.gemini_analyzer import GeminiAnalyzer, GeminiAPIError, GeminiRateLimitError
import base64
from io import BytesIO
from PIL import Image


@pytest.fixture
def mock_api_key():
    """Mock API key for testing."""
    return "test_api_key_12345"


@pytest.fixture
def sample_image_data():
    """Create a sample base64 encoded image."""
    img = Image.new('RGB', (200, 200), color='red')
    buffered = BytesIO()
    img.save(buffered, format="JPEG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/jpeg;base64,{img_str}"


@pytest.fixture
def analyzer(mock_api_key):
    """Create a GeminiAnalyzer instance for testing."""
    with patch('google.generativeai.configure'):
        with patch('google.generativeai.GenerativeModel'):
            return GeminiAnalyzer(mock_api_key, enable_cache=True)


class TestGeminiAnalyzer:
    """Test suite for GeminiAnalyzer."""

    def test_initialization(self, mock_api_key):
        """Test analyzer initialization."""
        with patch('google.generativeai.configure'):
            with patch('google.generativeai.GenerativeModel'):
                analyzer = GeminiAnalyzer(mock_api_key, enable_cache=True)

                assert analyzer.enable_cache is True
                assert analyzer.metrics['total_requests'] == 0
                assert 'business' in analyzer.consultant_prompts

    def test_cache_key_generation(self, analyzer, sample_image_data):
        """Test cache key generation."""
        key1 = analyzer._generate_cache_key(
            sample_image_data,
            "business",
            "test instructions"
        )
        key2 = analyzer._generate_cache_key(
            sample_image_data,
            "business",
            "test instructions"
        )
        key3 = analyzer._generate_cache_key(
            sample_image_data,
            "design",
            "test instructions"
        )

        # Same inputs should generate same key
        assert key1 == key2

        # Different consultant mode should generate different key
        assert key1 != key3

    @pytest.mark.asyncio
    async def test_successful_analysis(self, analyzer, sample_image_data):
        """Test successful frame analysis."""
        # Mock API response
        mock_response = MagicMock()
        mock_response.text = """
        ## KEY INSIGHTS
        - Insight 1
        - Insight 2

        ## RECOMMENDATIONS
        - Recommendation 1
        - Recommendation 2

        ## CONFIDENCE SCORE
        0.95
        """

        with patch.object(analyzer.model, 'generate_content', return_value=mock_response):
            result = await analyzer.analyze_frame(
                frame_data=sample_image_data,
                consultant_mode="business"
            )

            assert 'insights' in result
            assert 'recommendations' in result
            assert len(result['insights']) >= 1
            assert result['confidence_score'] >= 0.0
            assert analyzer.metrics['successful_requests'] == 1

    @pytest.mark.asyncio
    async def test_analysis_with_cache(self, analyzer, sample_image_data):
        """Test that caching works correctly."""
        mock_response = MagicMock()
        mock_response.text = "Test analysis result"

        with patch.object(analyzer.model, 'generate_content', return_value=mock_response):
            # First call - should hit API
            result1 = await analyzer.analyze_frame(
                frame_data=sample_image_data,
                consultant_mode="business"
            )

            # Second call with same params - should use cache
            result2 = await analyzer.analyze_frame(
                frame_data=sample_image_data,
                consultant_mode="business"
            )

            assert analyzer.metrics['cache_hits'] >= 1
            assert result1 is not None
            assert result2 is not None

    @pytest.mark.asyncio
    async def test_api_error_handling(self, analyzer, sample_image_data):
        """Test handling of API errors."""
        with patch.object(analyzer.model, 'generate_content', side_effect=Exception("API Error")):
            result = await analyzer.analyze_frame(
                frame_data=sample_image_data,
                consultant_mode="business"
            )

            # Should return fallback response
            assert result.get('is_fallback') is True
            assert 'error' in result
            assert analyzer.metrics['failed_requests'] >= 1

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, analyzer, sample_image_data):
        """Test handling of rate limit errors."""
        with patch.object(analyzer.model, 'generate_content',
                         side_effect=Exception("Rate limit exceeded")):
            result = await analyzer.analyze_frame(
                frame_data=sample_image_data,
                consultant_mode="business"
            )

            assert result.get('is_fallback') is True

    @pytest.mark.asyncio
    async def test_invalid_image_data(self, analyzer):
        """Test handling of invalid image data."""
        result = await analyzer.analyze_frame(
            frame_data="invalid_base64_data",
            consultant_mode="business"
        )

        assert result.get('is_fallback') is True
        assert 'error' in result

    @pytest.mark.asyncio
    async def test_all_consultant_modes(self, analyzer, sample_image_data):
        """Test all consultant modes."""
        modes = ['business', 'design', 'code', 'legal', 'medical', 'real_estate', 'marketing']

        mock_response = MagicMock()
        mock_response.text = "Analysis result"

        with patch.object(analyzer.model, 'generate_content', return_value=mock_response):
            for mode in modes:
                result = await analyzer.analyze_frame(
                    frame_data=sample_image_data,
                    consultant_mode=mode
                )

                assert result is not None
                assert result['consultant_mode'] == mode

    @pytest.mark.asyncio
    async def test_custom_instructions(self, analyzer, sample_image_data):
        """Test analysis with custom instructions."""
        mock_response = MagicMock()
        mock_response.text = "Custom analysis"

        with patch.object(analyzer.model, 'generate_content', return_value=mock_response):
            result = await analyzer.analyze_frame(
                frame_data=sample_image_data,
                consultant_mode="business",
                custom_instructions="Focus on ROI and financial metrics"
            )

            assert result is not None

    @pytest.mark.asyncio
    async def test_report_generation(self, analyzer):
        """Test session report generation."""
        analyses = [
            {
                'insights': ['Insight 1', 'Insight 2'],
                'recommendations': ['Rec 1', 'Rec 2']
            },
            {
                'insights': ['Insight 3'],
                'recommendations': ['Rec 3']
            }
        ]

        mock_response = MagicMock()
        mock_response.text = "# Executive Summary\nTest report"

        with patch.object(analyzer.model, 'generate_content', return_value=mock_response):
            report = await analyzer.generate_session_report(analyses, "business")

            assert "Executive Summary" in report

    @pytest.mark.asyncio
    async def test_fallback_report_generation(self, analyzer):
        """Test fallback report generation when API fails."""
        analyses = [
            {
                'insights': ['Insight 1'],
                'recommendations': ['Rec 1']
            }
        ]

        with patch.object(analyzer.model, 'generate_content', side_effect=Exception("API Error")):
            report = await analyzer.generate_session_report(analyses, "business")

            # Should generate basic report
            assert "Session Summary" in report or "Aggregated Insights" in report

    def test_metrics_tracking(self, analyzer):
        """Test metrics are tracked correctly."""
        metrics = analyzer.get_metrics()

        assert 'total_requests' in metrics
        assert 'successful_requests' in metrics
        assert 'failed_requests' in metrics
        assert 'cache_hit_rate' in metrics
        assert 'success_rate' in metrics

    def test_cache_clearing(self, analyzer):
        """Test cache can be cleared."""
        analyzer.cache['test_key'] = 'test_value'
        assert len(analyzer.cache) > 0

        analyzer.clear_cache()
        assert len(analyzer.cache) == 0

    def test_parse_analysis_sections(self, analyzer):
        """Test parsing of analysis sections."""
        text = """
        ## KEY INSIGHTS
        - First insight
        - Second insight

        ## RECOMMENDATIONS
        - First recommendation
        - Second recommendation

        ## RISK FACTORS
        - High priority risk
        - Medium priority risk

        ## CONFIDENCE SCORE
        0.92
        """

        result = analyzer._parse_analysis(text)

        assert len(result['insights']) == 2
        assert len(result['recommendations']) == 2
        assert len(result['risk_factors']) == 2
        assert result['confidence_score'] == 0.92

    def test_fallback_response(self, analyzer):
        """Test fallback response generation."""
        fallback = analyzer._get_fallback_response("business", "Test error")

        assert fallback['is_fallback'] is True
        assert fallback['consultant_mode'] == "business"
        assert 'error' in fallback
        assert len(fallback['insights']) > 0
        assert fallback['confidence_score'] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
