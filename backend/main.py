from fastapi import FastAPI
from pydantic import BaseModel
from pathlib import Path
import json, re, sqlite3, uuid
from datetime import datetime

# FAISS imports
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer


# ============================================================
# PATHS
# ============================================================

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

DEVICES_FILE = DATA / "devices.json"
COMMANDS_FILE = DATA / "commands.json"
DB_FILE = BASE / "audit.db"


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Voice Configuration Agent Prototype")


# ============================================================
# LOAD DATA
# ============================================================

devices = json.loads(DEVICES_FILE.read_text())
commands = json.loads(COMMANDS_FILE.read_text())

failure_mode = False
pending = {}


# ============================================================
# FAISS SETUP
# ============================================================

# Sentence Transformer converts text into numerical embeddings.
# FAISS then searches those embeddings for the most similar
# approved command template.

embedding_model = SentenceTransformer("all-MiniLM-L6-v2")


def command_to_text(command):
    """
    Convert an approved command from commands.json
    into searchable text.
    """

    intent = command.get("intent", "")
    description = command.get("description", "")
    keywords = command.get("keywords", [])

    return " ".join([
        intent,
        description,
        " ".join(keywords)
    ]).strip()


# Create searchable text for every approved command.
command_texts = [
    command_to_text(command)
    for command in commands
]


# Convert command text into embeddings.
command_embeddings = embedding_model.encode(
    command_texts,
    convert_to_numpy=True
).astype("float32")


# Normalize embeddings.
# This allows FAISS inner-product search to behave
# approximately like cosine similarity.
faiss.normalize_L2(command_embeddings)


# Number of dimensions in each embedding.
dimension = command_embeddings.shape[1]


# Create FAISS index.
command_index = faiss.IndexFlatIP(dimension)


# Add all approved command embeddings to FAISS.
command_index.add(command_embeddings)


# Minimum similarity required to accept a result.
#
# If FAISS gives a score lower than this value,
# we reject the command instead of guessing.
FAISS_MIN_SCORE = 0.35


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            request TEXT,
            intent TEXT,
            command_id TEXT,
            device TEXT,
            interface TEXT,
            old_value TEXT,
            new_value TEXT,
            confirmed TEXT,
            result TEXT,
            message TEXT
        )
    """)

    conn.commit()

    return conn


# ============================================================
# AUDIT LOG
# ============================================================

def audit(
    request,
    intent="",
    command_id="",
    device="",
    interface="",
    old_value="",
    new_value="",
    confirmed="",
    result="",
    message=""
):
    c = db()

    c.execute("""
        INSERT INTO audit
        (
            time,
            request,
            intent,
            command_id,
            device,
            interface,
            old_value,
            new_value,
            confirmed,
            result,
            message
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        request,
        intent,
        command_id,
        device,
        interface,
        str(old_value),
        str(new_value),
        str(confirmed),
        result,
        message
    ))

    c.commit()
    c.close()


# ============================================================
# NORMALIZATION
# ============================================================

def norm(s):

    s = s.lower().strip()

    replacements = {
        "zero": "0",
        "one": "1",
        "two": "2",
        "three": "3",
        "slash": "/",

        "cmg zero two": "cmg-02",
        "cmg two": "cmg-02",

        "show bjp": "show bgp",
        "bjp neighbours": "bgp neighbours",
        "bjp neighbors": "bgp neighbors"
    }

    for a, b in replacements.items():
        s = s.replace(a, b)

    return s


# ============================================================
# DEVICE EXTRACTION
# ============================================================

def get_device(text):

    m = re.search(
        r"\b(cmg[- ]?\d+)\b",
        text,
        re.I
    )

    if not m:
        return None

    d = m.group(1).upper().replace(" ", "-")

    return d if d in devices else None


# ============================================================
# INTERFACE EXTRACTION
# ============================================================

def get_interface(text):

    m = re.search(
        r"\bge[- ]?(\d+)/(\d+)/(\d+)\b",
        text,
        re.I
    )

    if m:
        return f"ge-{m.group(1)}/{m.group(2)}/{m.group(3)}"

    m = re.search(
        r"\bge-\d+/\d+/\d+\b",
        text,
        re.I
    )

    if m:
        return m.group(0)

    return None


# ============================================================
# INTENT DETECTION
# ============================================================

def detect_intent(text):

    t = norm(text)

    rules = [

        (
            "SET_MTU",
            [
                "set mtu",
                "change mtu",
                "configure mtu"
            ]
        ),

        (
            "ENABLE_INTERFACE",
            [
                "enable interface",
                "no shutdown",
                "bring interface up"
            ]
        ),

        (
            "DISABLE_INTERFACE",
            [
                "disable interface",
                "shutdown interface",
                "bring interface down"
            ]
        ),

        (
            "SHOW_BGP",
            [
                "show bgp",
                "bgp neighbour",
                "bgp neighbor"
            ]
        ),

        (
            "SHOW_INTERFACE",
            [
                "show interface",
                "interface status",
                "show interfaces"
            ]
        ),

        (
            "SHOW_MTU",
            [
                "show mtu",
                "current mtu"
            ]
        ),

        (
            "SHOW_DEVICE",
            [
                "device information",
                "show device",
                "device info",
                "system information"
            ]
        ),

        (
            "SHOW_CONFIG",
            [
                "show configuration",
                "show config"
            ]
        )
    ]

    for intent, keys in rules:

        if any(k in t for k in keys):
            return intent

    return None


# ============================================================
# FAISS COMMAND RETRIEVAL
# ============================================================

def retrieve(intent, text):

    """
    Retrieve the most semantically similar approved command.

    Safety:
    FAISS is only allowed to select commands belonging
    to the already detected intent.
    """

    # Find indexes of commands having the detected intent.
    candidate_indices = [
        i
        for i, command in enumerate(commands)
        if command.get("intent") == intent
    ]

    if not candidate_indices:
        return None


    # --------------------------------------------------------
    # Convert user's request into an embedding
    # --------------------------------------------------------

    query_embedding = embedding_model.encode(
        [text],
        convert_to_numpy=True
    ).astype("float32")


    # Normalize query vector.
    faiss.normalize_L2(query_embedding)


    # --------------------------------------------------------
    # Search FAISS
    # --------------------------------------------------------

    scores, indices = command_index.search(
        query_embedding,
        len(commands)
    )


    # --------------------------------------------------------
    # Select the highest scoring command that belongs
    # to the detected intent.
    # --------------------------------------------------------

    for score, index in zip(
        scores[0],
        indices[0]
    ):

        index = int(index)
        score = float(score)

        if index not in candidate_indices:
            continue


        # Reject low-confidence matches.
        if score < FAISS_MIN_SCORE:
            return None


        # Copy command so we don't modify commands.json data.
        selected_command = dict(commands[index])


        # Keep the score so it can be displayed/debugged.
        selected_command["faiss_score"] = round(
            score,
            4
        )


        return selected_command


    return None


# ============================================================
# REQUEST PARSING
# ============================================================

def parse_request(request):

    text = norm(request)

    intent = detect_intent(text)

    device = get_device(text)

    interface = get_interface(text)

    mtu = None


    if intent == "SET_MTU":

        m = re.search(
            r"\bmtu\b(?:\s*(?:to|=))?\s*(\d{3,5})",
            text
        )

        if m:
            mtu = int(m.group(1))


    return intent, device, interface, mtu


# ============================================================
# REQUIRED FIELDS
# ============================================================

def require_fields(
    intent,
    device,
    interface,
    mtu
):

    missing = []


    if not device:
        missing.append(
            "device name (example: CMG-02)"
        )


    if intent in (
        "SET_MTU",
        "ENABLE_INTERFACE",
        "DISABLE_INTERFACE",
        "SHOW_INTERFACE",
        "SHOW_MTU"
    ) and not interface:

        missing.append(
            "interface (example: ge-0/0/1)"
        )


    if intent == "SET_MTU" and mtu is None:

        missing.append(
            "MTU value (example: 1500)"
        )


    return missing


# ============================================================
# REQUEST BODY
# ============================================================

class RequestBody(BaseModel):

    request: str


# ============================================================
# CONFIRMATION BODY
# ============================================================

class ConfirmBody(BaseModel):

    token: str

    confirm: bool = True


# ============================================================
# FAILURE MODE BODY
# ============================================================

class FailureBody(BaseModel):

    enabled: bool


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": "voice-config-agent-prototype"
    }


# ============================================================
# DEVICES
# ============================================================

@app.get("/devices")
def get_devices():

    return devices


# ============================================================
# AUDIT
# ============================================================

@app.get("/audit")
def get_audit():

    c = db()

    rows = c.execute(
        "SELECT * FROM audit ORDER BY id DESC LIMIT 100"
    ).fetchall()

    c.close()


    cols = [
        "id",
        "time",
        "request",
        "intent",
        "command_id",
        "device",
        "interface",
        "old_value",
        "new_value",
        "confirmed",
        "result",
        "message"
    ]


    return [
        dict(zip(cols, row))
        for row in rows
    ]


# ============================================================
# FAILURE MODE
# ============================================================

@app.post("/failure-mode")
def set_failure(body: FailureBody):

    global failure_mode

    failure_mode = body.enabled

    return {
        "failure_mode": failure_mode
    }


# ============================================================
# MAIN REQUEST
# ============================================================

@app.post("/request")
def request(body: RequestBody):

    request_text = body.request.strip()


    if not request_text:

        return {
            "ok": False,
            "message": "Please enter a request."
        }


    # --------------------------------------------------------
    # Parse request
    # --------------------------------------------------------

    intent, device, interface, mtu = parse_request(
        request_text
    )


    # --------------------------------------------------------
    # Check intent
    # --------------------------------------------------------

    if not intent:

        audit(
            request_text,
            result="REJECTED",
            message="No approved intent/template was found."
        )

        return {
            "ok": False,
            "message":
                "No approved command/template was found. "
                "I will not invent a command."
        }


    # --------------------------------------------------------
    # FAISS command retrieval
    # --------------------------------------------------------

    command = retrieve(
        intent,
        request_text
    )


    if not command:

        audit(
            request_text,
            intent=intent,
            result="REJECTED",
            message="FAISS could not find a sufficiently similar approved command."
        )

        return {
            "ok": False,
            "message":
                "No sufficiently similar approved "
                "command/template was found."
        }


    # --------------------------------------------------------
    # Required fields
    # --------------------------------------------------------

    missing = require_fields(
        intent,
        device,
        interface,
        mtu
    )


    if missing:

        return {
            "ok": False,
            "needs_information": missing,
            "intent": intent,
            "message":
                "I need more information before "
                "I can create a plan."
        }


    # --------------------------------------------------------
    # Validate device
    # --------------------------------------------------------

    if device not in devices:

        return {
            "ok": False,
            "message": f"Unknown device: {device}"
        }


    # --------------------------------------------------------
    # Validate interface
    # --------------------------------------------------------

    if (
        interface
        and interface not in devices[device]["interfaces"]
    ):

        return {
            "ok": False,
            "message":
                f"Unknown interface {interface} on {device}."
        }


    d = devices[device]


    # --------------------------------------------------------
    # Build result
    # --------------------------------------------------------

    result = {

        "ok": True,

        "intent": intent,

        "command": command,

        "device": device,

        "interface": interface,

        "mtu": mtu,

        "requires_confirmation":
            command["type"] == "configuration",

        "failure_mode": failure_mode
    }


    # ========================================================
    # READ-ONLY OPERATIONS
    # ========================================================

    if intent == "SHOW_BGP":

        peers = d["bgp_neighbours"]

        result["answer"] = {

            "total": len(peers),

            "up": sum(
                p["state"] == "Established"
                for p in peers
            ),

            "down": sum(
                p["state"] != "Established"
                for p in peers
            ),

            "peers": peers
        }


    elif intent == "SHOW_INTERFACE":

        result["answer"] = (
            d["interfaces"][interface]
        )


    elif intent == "SHOW_MTU":

        result["answer"] = {

            "interface": interface,

            "mtu":
                d["interfaces"][interface]["mtu"]
        }


    elif intent == "SHOW_DEVICE":

        result["answer"] = {
            k: d[k]
            for k in (
                "vendor",
                "model",
                "os"
            )
        }


    elif intent == "SHOW_CONFIG":

        result["answer"] = d["config"]


    # ========================================================
    # SET MTU
    # ========================================================

    elif intent == "SET_MTU":

        old = d["interfaces"][interface]["mtu"]

        result.update({

            "old_value": old,

            "new_value": mtu,

            "dry_run":
                f"{device} {interface}: "
                f"MTU {old} -> {mtu}"
        })


    # ========================================================
    # ENABLE / DISABLE INTERFACE
    # ========================================================

    elif intent in (
        "ENABLE_INTERFACE",
        "DISABLE_INTERFACE"
    ):

        old = d["interfaces"][interface]["status"]

        new = (
            "up"
            if intent == "ENABLE_INTERFACE"
            else "down"
        )

        result.update({

            "old_value": old,

            "new_value": new,

            "dry_run":
                f"{device} {interface}: "
                f"status {old} -> {new}"
        })


    # ========================================================
    # CONFIRMATION REQUIRED
    # ========================================================

    if result["requires_confirmation"]:

        token = uuid.uuid4().hex

        pending[token] = {

            "request": request_text,

            "result": result
        }

        result["token"] = token


        audit(
            request_text,
            intent,
            command["id"],
            device,
            interface,
            result.get("old_value", ""),
            result.get("new_value", ""),
            "PENDING",
            "DRY_RUN",
            result.get("dry_run", "")
        )


    # ========================================================
    # READ-ONLY OPERATION
    # ========================================================

    else:

        audit(
            request_text,
            intent,
            command["id"],
            device,
            interface,
            "",
            "",
            "NOT_REQUIRED",
            "SUCCESS",
            "Read-only operation"
        )


    return result


# ============================================================
# CONFIRM
# ============================================================

@app.post("/confirm")
def confirm(body: ConfirmBody):

    item = pending.pop(
        body.token,
        None
    )


    if not item:

        return {
            "ok": False,
            "message":
                "This confirmation has expired "
                "or was already handled."
        }


    result = item["result"]

    req = item["request"]

    intent = result["intent"]

    device = result["device"]

    interface = result["interface"]

    command_id = result["command"]["id"]


    # ========================================================
    # USER CANCELLED
    # ========================================================

    if not body.confirm:

        audit(
            req,
            intent,
            command_id,
            device,
            interface,
            result.get("old_value", ""),
            result.get("new_value", ""),
            "NO",
            "CANCELLED",
            "User cancelled; no change applied."
        )


        return {

            "ok": True,

            "status": "cancelled",

            "message":
                "Cancelled. No configuration was changed."
        }


    # ========================================================
    # APPLY CONFIGURATION
    # ========================================================

    d = devices[device]

    old = result["old_value"]


    # ========================================================
    # SET MTU
    # ========================================================

    if intent == "SET_MTU":

        d["interfaces"][interface]["mtu"] = (
            result["new_value"]
        )

        expected = result["new_value"]

        actual = (
            d["interfaces"][interface]["mtu"]
        )


        # Simulate failure.
        if failure_mode:

            actual = old


        # ----------------------------------------------------
        # Verification failed
        # ----------------------------------------------------

        if actual != expected:

            d["interfaces"][interface]["mtu"] = old

            msg = (
                "Verification failed, so the previous "
                "configuration was automatically restored."
            )


            audit(
                req,
                intent,
                command_id,
                device,
                interface,
                old,
                result["new_value"],
                "YES",
                "ROLLED_BACK",
                msg
            )


            return {

                "ok": True,

                "status": "rollback",

                "message": msg,

                "expected": expected,

                "actual": actual,

                "restored": old
            }


    # ========================================================
    # ENABLE / DISABLE INTERFACE
    # ========================================================

    else:

        new = result["new_value"]

        d["interfaces"][interface]["status"] = new

        actual = (
            d["interfaces"][interface]["status"]
        )

        verified = actual == new


        # Simulate failure.
        if failure_mode:

            verified = False


        # ----------------------------------------------------
        # Verification failed
        # ----------------------------------------------------

        if not verified:

            d["interfaces"][interface]["status"] = old

            msg = (
                "Verification failed, so the previous "
                "interface status was restored."
            )


            audit(
                req,
                intent,
                command_id,
                device,
                interface,
                old,
                new,
                "YES",
                "ROLLED_BACK",
                msg
            )


            return {

                "ok": True,

                "status": "rollback",

                "message": msg,

                "restored": old
            }


    # ========================================================
    # SAVE DEVICE STATE
    # ========================================================

    DEVICES_FILE.write_text(
        json.dumps(
            devices,
            indent=2
        )
    )


    # ========================================================
    # SUCCESS AUDIT
    # ========================================================

    audit(
        req,
        intent,
        command_id,
        device,
        interface,
        old,
        result["new_value"],
        "YES",
        "SUCCESS",
        "Applied and verified successfully."
    )


    return {

        "ok": True,

        "status": "success",

        "message":
            "Configuration applied and verified successfully.",

        "expected":
            result["new_value"],

        "actual":
            result["new_value"]
    }