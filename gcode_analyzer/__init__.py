"""G-code toolpath analyser: parse, interpolate, plan, measure."""

from .analyzer import AnalysisResult, analyze
from .models import Config, ConfigError

__all__ = ["analyze", "AnalysisResult", "Config", "ConfigError"]
__version__ = "1.0.0"
