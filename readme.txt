# Stock Fundamentals Analyzer - Startup Instructions

## 1. Initial Setup (Run Once After Moving Folder)
Since the folder path has a space (`!bolsa diario`), use these exact commands in your Command Prompt to rebuild the virtual environment.

Open Command Prompt (cmd) and run these one by one:
```cmd
:: 1. Navigate to the new folder (Quotes are REQUIRED because of the space)
cd "c:\!diego\!bolsa diario\stock-analyzer"

:: 2. Delete the old broken virtual environment (if it exists)
rmdir /s /q .venv

:: 3. Create a fresh virtual environment
py -m venv .venv

:: 4. Activate it (Notice there is NO ampersand & in cmd)
".\.venv\Scripts\activate.bat"

:: 5. Install the required packages
pip install fastapi uvicorn requests python-dotenv yfinance




2. Daily Startup (How to run it normally)
Every time you want to use the analyzer, open your Command Prompt and run:


:: 1. Navigate to the folder
cd "c:\!diego\!bolsa diario\stock-analyzer"

:: 2. Activate the virtual environment
".\.venv\Scripts\activate.bat"

:: 3. Go into the backend folder
cd backend

:: 4. Start the server
uvicorn main:app --reload --host 127.0.0.1 --port 8000

\index.html
