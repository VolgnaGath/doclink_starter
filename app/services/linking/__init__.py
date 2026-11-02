"""Pure linking engine. Persistence and evaluation are deliberately outside this package."""

from app.services.linking.methods import link
from app.services.linking.types import LinkingConfig, LinkingInput, LinkingResult

__all__ = ["LinkingConfig", "LinkingInput", "LinkingResult", "link"]
