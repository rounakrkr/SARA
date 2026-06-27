import os
import sys
from pathlib import Path

import uvicorn

# Ensure the project root is always on sys.path, regardless of how
# the file is invoked (python main.py, uvicorn main:app, etc.)
project_dir = Path(__file__).resolve().parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from bootstrap import build_asgi_application

# Exposed at module level so external ASGI runners can find it:
#   uvicorn main:app --host 0.0.0.0 --workers 4
app = build_asgi_application()


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port_str = os.getenv("PORT", "8000")
    try:
        port = int(port_str)
    except ValueError:
        print(f"Invalid PORT value '{port_str}', falling back to 8000")
        port = 8000

    print(f"Starting SARA FastAPI server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()