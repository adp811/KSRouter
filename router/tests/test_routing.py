import pytest
import asyncio
from src.routing import heuristic_route, classifier_route, determine_tier, MODEL_ENDPOINTS


class TestHeuristicRouting:
    """Test heuristic-based routing logic."""
    
    @pytest.mark.asyncio
    async def test_small_factual_question(self):
        tier = await heuristic_route("What is the capital of France?")
        assert tier == "small"
    
    @pytest.mark.asyncio
    async def test_small_math(self):
        tier = await heuristic_route("What is 2+2?")
        assert tier == "small"
    
    @pytest.mark.asyncio
    async def test_small_who_question(self):
        tier = await heuristic_route("Who is the current president of the United States?")
        assert tier == "small"
    
    @pytest.mark.asyncio
    async def test_medium_code_explanation(self):
        tier = await heuristic_route("Can you explain how this Python function works? It takes a list and returns a sorted version.")
        assert tier == "medium"
    
    @pytest.mark.asyncio
    async def test_medium_multi_step(self):
        tier = await heuristic_route("Explain the difference between REST and GraphQL, and when to use each one.")
        assert tier == "medium"
    
    @pytest.mark.asyncio
    async def test_large_complex_analysis(self):
        tier = await heuristic_route("Provide a comprehensive analysis of the economic impacts of artificial intelligence on the global workforce, including specific examples from manufacturing, healthcare, and software development industries. Consider both short-term disruptions and long-term structural changes.")
        assert tier == "large"
    
    @pytest.mark.asyncio
    async def test_large_complex_code_generation(self):
        tier = await heuristic_route("Implement a red-black tree in Python with insert, delete, and search operations. Include unit tests and explain the time complexity of each operation.")
        assert tier == "large"
    
    @pytest.mark.asyncio
    async def test_large_code_block(self):
        tier = await heuristic_route("```python\ndef fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)\n```\nExplain this code and optimize it.")
        assert tier == "large"
    
    @pytest.mark.asyncio
    async def test_large_complex_keyword(self):
        tier = await heuristic_route("Write a detailed essay about the impacts of climate change")
        assert tier == "large"


class TestClassifierRouting:
    """Test model-as-classifier routing."""
    
    @pytest.mark.asyncio
    async def test_classifier_timeout_fallback(self, monkeypatch):
        """Test that classifier falls back to heuristic when httpx raises a timeout.

        This mocks `post` to raise the same exception type httpx actually
        raises when a request exceeds its configured timeout
        (`httpx.TimeoutError` and subclasses such as `ReadTimeout`), rather
        than sleeping in real time, so the test is both fast and exercises
        the real `except httpx.TimeoutError` branch in `classifier_route`.
        """
        import httpx

        async def mock_post(*args, **kwargs):
            raise httpx.ReadTimeout("simulated timeout", request=None)

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        result = await classifier_route("What is 2+2?", timeout=0.001)
        assert result is None

    @pytest.mark.asyncio
    async def test_classifier_generic_error_fallback(self, monkeypatch):
        """Test that classifier falls back to heuristic on non-timeout errors too."""
        import httpx

        async def mock_post(*args, **kwargs):
            raise httpx.ConnectError("simulated connection error", request=None)

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        result = await classifier_route("What is 2+2?", timeout=2.0)
        assert result is None


class TestDetermineTier:
    """Test the full tier determination logic."""
    
    @pytest.mark.asyncio
    async def test_explicit_override_large(self):
        tier, method = await determine_tier("What is 2+2?", explicit_tier="large")
        assert tier == "large"
        assert method == "explicit"
    
    @pytest.mark.asyncio
    async def test_explicit_override_medium(self):
        tier, method = await determine_tier("Complex analysis here", explicit_tier="medium")
        assert tier == "medium"
        assert method == "explicit"
    
    @pytest.mark.asyncio
    async def test_no_explicit_uses_classifier_or_heuristic(self):
        tier, method = await determine_tier("What is 2+2?")
        assert tier in ["small", "medium", "large"]
        assert method in ["heuristic", "classifier"]


class TestModelEndpoints:
    """Test model endpoint configuration."""
    
    def test_endpoints_configured(self):
        assert "small" in MODEL_ENDPOINTS
        assert "medium" in MODEL_ENDPOINTS
        assert "large" in MODEL_ENDPOINTS
        
        for tier, url in MODEL_ENDPOINTS.items():
            assert url.startswith("http://")
            assert "predictor" in url or "svc" in url
