"""reconcile — declarative cross-object field resolution for Pydantic models.

``dependency`` declares cross-object field derivations and validators.
``reconcile`` resolves all dependencies to a consistent state.
"""

from reconcile.core import Unresolvable as Unresolvable
from reconcile.core import dependency as dependency
from reconcile.core import reconcile as reconcile
