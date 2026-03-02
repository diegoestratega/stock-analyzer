from pathlib import Path
import sys

# Point Vercel to your existing backend folder
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Import your FastAPI app
import main as backend_main
app = backend_main.app
