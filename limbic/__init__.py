"""limbic: a pluggable artificial emotion system for LLM agents."""
from .agent import Limbic
from .appraisal import Appraisal, KeywordAppraiser, LLMAppraiser, map_emotions
from .emotions import EMOTIONS
from .engine import Engine, State
from .store import Store
from .temperament import Temperament

__version__ = "0.9.0"
__all__ = ["Limbic", "Appraisal", "KeywordAppraiser", "LLMAppraiser", "map_emotions",
           "EMOTIONS", "Engine", "State", "Store", "Temperament", "__version__"]
