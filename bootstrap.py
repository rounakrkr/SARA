"""
Application bootstrap — loads config and wires it into the FastAPI app.

Directory layout (as of current state):
    SARA/
    ├── app.py              ← FastAPI app + AppState (brain, sessions, websockets)
    ├── core/               ← brain.py, pipeline.py, personality.py, types.py
    ├── memory/             ← session.py, long_term.py, lore.py
    ├── config/
    │   ├── loader.py       ← load_config() → AppConfig dataclass
    │   └── settings.json   ← companion name, logging level, service defaults
    ├── utils/
    │   └── logging.py      ← configure_logging()
    ├── bootstrap.py        ← this file
    └── main.py             ← entry point

Note: load_dotenv() is called here (via config/loader.py) and NOT in app.py
to avoid double-loading. app.py's own load_dotenv() call was left in place
as a belt-and-suspenders fallback for cases where app.py is imported
directly (e.g. tests), which is harmless since python-dotenv skips
already-set env vars.
"""

from pathlib import Path

from app import app, state
from config.loader import load_config
from utils.logging import configure_logging


def build_asgi_application(config_path: str | None = None):
    """
    Loads SARA's config and wires it into the FastAPI app.

    sara/app.py builds `app` and the global `state` (AppState, which holds
    the single shared CoreBrain) eagerly at import time, with the companion
    name hardcoded to "Sara". This patches the companion name from
    config/settings.json right after import — before uvicorn ever starts
    serving requests, so it's functionally identical to constructing it
    correctly from the start.

    Returns the fully configured FastAPI app object, ready for uvicorn.
    """
    base_dir = Path(__file__).resolve().parent
    resolved_config_path = (
        Path(config_path) if config_path else base_dir / "config" / "settings.json"
    )

    config = load_config(resolved_config_path)
    configure_logging(config.logging.level)

    # Patch the shared brain's identity from config instead of the
    # hardcoded "Sara" baked into app.py's AppState().
    state.brain.companion_name = config.assistant.name

    return app