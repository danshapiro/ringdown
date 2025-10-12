# Suppress Pydantic v1 class-based config deprecation warnings emitted by third-party libs
import warnings

__all__ = ["__version__"]
__version__ = "0.2.0"

warnings.filterwarnings(
    "ignore",
    message="Support for class-based `config` is deprecated, use ConfigDict instead.",
    category=DeprecationWarning,
)