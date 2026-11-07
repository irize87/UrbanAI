import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.db.schema import connect

with connect() as conn:
    row = conn.execute(
        "SELECT value FROM meta WHERE key='terrain_grid'"
    ).fetchone()
data = json.loads(row["value"])
print("source=", data.get("source"))
print("dataset=", data.get("dataset"))
print("relief=", data.get("relief_m"))
print("grid=", len(data["grid"]), "x", len(data["grid"][0]))
