# Voice Configuration Agent Prototype

## What it demonstrates
Voice/Text request -> Intent detection -> Approved command/template -> Safety check -> Dry run -> User confirmation -> Simulated device -> Verification -> Rollback -> Audit log.

This is a local simulator. It does not connect to a real Nokia network and does not require PuTTY.

## Run

Open Terminal 1:
```powershell
cd "C:\Users\nisha\Downloads\voice-config-agent"
..\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --port 8000
```

Open Terminal 2:
```powershell
cd "C:\Users\nisha\Downloads\voice-config-agent"
..\.venv\Scripts\Activate.ps1
streamlit run frontend/app.py
```

If your `.venv` is outside the project folder, adjust the activation path.

## Test
- Show BGP neighbours on CMG-02
- Show interface status on ge-0/0/1 of CMG-02
- Set MTU 1500 on ge-0/0/1 of CMG-02
- Enable interface ge-0/0/2 on CMG-02
- Disable interface ge-0/0/1 on CMG-02

Use the sidebar failure mode to demonstrate automatic rollback.
