"""Root conftest so fixtures apply across both test locations.

Cross-cutting infra tests live under tests/; each agent's own tests live next to its code
in agents/<name>/tests/. Fixtures are defined once in tests/conftest.py and re-exported
here so pytest's directory-based discovery reaches both.
"""

from tests.conftest import (  # noqa: F401
    agents_config,
    clean_registries,
    db,
    engine,
    make_policy,
    policy,
    tools_registry,
)
