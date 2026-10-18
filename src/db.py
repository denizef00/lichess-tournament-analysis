import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "lichess.db"
SCHEMA_PATH = ROOT / "sql" / "schema.sql"


def connect(path=DB_PATH):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   
    con.row_factory = sqlite3.Row
    return con


def init_schema(con):
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))