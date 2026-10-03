"""Business modules. Each one depends on `app.platform`, never on another module's internals.

`MODULES` is the literal list of installed modules, read only by the composition root.
"""

from app.modules.commercial import MODULE as COMMERCIAL
from app.platform.modules import ModuleSpec

MODULES: tuple[ModuleSpec, ...] = (COMMERCIAL,)
