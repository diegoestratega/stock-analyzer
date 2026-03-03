from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# (1) Your existing /api routes must be created here
# e.g. app.get("/api/health"), app.get("/api/analyze/{ticker}") ...

# (2) LAST: mount frontend at "/"
app.mount("/", StaticFiles(directory="static", html=True), name="frontend")
