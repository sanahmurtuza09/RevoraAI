import os
import csv
import random
import math
import datetime
import sqlite3
from pathlib import Path
from io import BytesIO
from collections import Counter

import pandas as pd
import altair as alt
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

import matplotlib.pyplot as plt
import streamlit.components.v1 as components


# ----------------------------
# Setup
# ----------------------------
st.set_page_config(
    page_title="RevoraAI — Self Reflection & Clinician Insight Prototype",
    page_icon="🪞",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("RevoraAI")
st.subheader("AI-powered Self reflection and Clinician insight prototype")


# ----------------------------
# Load API key
# ----------------------------
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=api_key) if api_key else None

DB_PATH = Path("data") / "revoraai.db"
EVENTS_FILE = "events.csv"


# ----------------------------
# Constants
# ----------------------------
SUGGESTED_TAGS = [
    "sensory overload",
    "quiet time",
    "social interaction",
    "work stress",
    "fatigue",
    "sleep issues",
    "anxiety",
    "focus/flow",
    "meltdown/shutdown",
    "routine change",
    "therapy win",
    "self-care",
    "food/appetite",
    "exercise/movement",
]

TAG_CATEGORY = {
    "therapy win": "positive",
    "self-care": "positive",
    "focus/flow": "positive",
    "exercise/movement": "positive",
    "quiet time": "positive",
    "routine change": "neutral",
    "social interaction": "neutral",
    "food/appetite": "neutral",
    "sensory overload": "negative",
    "work stress": "negative",
    "fatigue": "negative",
    "sleep issues": "negative",
    "anxiety": "negative",
    "meltdown/shutdown": "negative",
}


# ----------------------------
# DB Helpers
# ----------------------------
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def attention_items_for_range(df_range: pd.DataFrame, start_day: datetime.date, end_day: datetime.date) -> list[str]:
    items = []
    if df_range.empty:
        return items

    # engaged days in the selected range
    engaged_days = df_range["day"].nunique()
    range_days = (end_day - start_day).days + 1
    if range_days >= 7 and engaged_days <= 2:
        items.append("Fewer check-ins in this period (2 days or less).")

    # high event volume on a single day (in range)
    ev = df_range[df_range["entry_type"] == "event"]
    if not ev.empty:
        counts = ev.groupby("day").size().sort_values(ascending=False)
        top_day = counts.index[0]
        top_count = int(counts.iloc[0])
        if top_count >= 3:
            items.append(f"One day had several event check-ins ({top_count} on {top_day}).")

    # keyword counts (in range)
    def count_like(keys):
        total = 0
        for _, row in df_range.iterrows():
            tags = (row["tags"] or "").lower()
            note = (row["note"] or "").lower()
            if any(k in tags or k in note for k in keys):
                total += 1
        return total

    sleep_mentions = count_like(["sleep"])
    if sleep_mentions >= 2:
        items.append(f"Sleep came up multiple times in this period ({sleep_mentions}).")

    sensory_mentions = count_like(["sensory", "overload"])
    if sensory_mentions >= 2:
        items.append(f"Sensory load came up multiple times in this period ({sensory_mentions}).")

    shutdown_mentions = count_like(["shutdown", "meltdown"])
    if shutdown_mentions >= 2:
        items.append(f"Shutdown/meltdown themes appeared more than once in this period ({shutdown_mentions}).")

    return items

def table_exists(conn, name: str) -> bool:
    r = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return r is not None


def init_db():
    """
    entries(id PK, timestamp, day, entry_type, mood, tags, note)

    - daily check-in: entry_type='daily' (1/day, upsert)
    - event check-ins: entry_type='event' (many/day)
    """
    conn = get_conn()

    # migrate old checkins table if present and entries not present
    if table_exists(conn, "checkins") and (not table_exists(conn, "entries")):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp TEXT NOT NULL,
              day TEXT NOT NULL,
              entry_type TEXT NOT NULL CHECK(entry_type IN ('daily','event')),
              mood INTEGER NOT NULL CHECK(mood BETWEEN 1 AND 10),
              tags TEXT DEFAULT '',
              note TEXT DEFAULT ''
            );
        """)
        rows = conn.execute("SELECT day, mood, note FROM checkins").fetchall()
        for row in rows:
            day = row["day"]
            ts = f"{day}T12:00:00"
            note = row["note"] or ""
            tags = ""
            if "Tags:" in note:
                tags_part = note.split("Tags:", 1)[1].split("|", 1)[0].strip()
                tags = tags_part
            conn.execute(
                """
                INSERT INTO entries (timestamp, day, entry_type, mood, tags, note)
                VALUES (?, ?, 'daily', ?, ?, ?)
                """,
                (ts, day, int(row["mood"]), tags, note[:500]),
            )
        conn.commit()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp TEXT NOT NULL,
          day TEXT NOT NULL,
          entry_type TEXT NOT NULL CHECK(entry_type IN ('daily','event')),
          mood INTEGER NOT NULL CHECK(mood BETWEEN 1 AND 10),
          tags TEXT DEFAULT '',
          note TEXT DEFAULT ''
        );
    """)

    # one daily check-in per day
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_daily_unique
        ON entries(day)
        WHERE entry_type='daily';
    """)
    conn.commit()


def iso_day(d: datetime.date) -> str:
    return d.isoformat()


def now_iso() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def save_daily_checkin(day: datetime.date, mood: int, tags: list[str], note: str):
    """
    Daily check-in: upsert by day, and ALSO update timestamp so the "recent entries" table reflects the latest save time.
    """
    init_db()
    conn = get_conn()
    day_s = iso_day(day)
    ts = now_iso()
    tags_s = ", ".join([t.strip() for t in tags if t.strip()])[:300]
    note_s = (note or "").strip()[:500]

    conn.execute(
        """
        INSERT INTO entries (timestamp, day, entry_type, mood, tags, note)
        VALUES (?, ?, 'daily', ?, ?, ?)
        ON CONFLICT(day) WHERE entry_type='daily'
        DO UPDATE SET
          timestamp=excluded.timestamp,
          mood=excluded.mood,
          tags=excluded.tags,
          note=excluded.note
        """,
        (ts, day_s, int(mood), tags_s, note_s),
    )
    conn.commit()


def save_event_checkin(mood: int, tags: list[str], note: str):
    init_db()
    conn = get_conn()
    ts = now_iso()
    day_s = ts.split("T", 1)[0]
    tags_s = ", ".join([t.strip() for t in tags if t.strip()])[:300]
    note_s = (note or "").strip()[:500]

    conn.execute(
        """
        INSERT INTO entries (timestamp, day, entry_type, mood, tags, note)
        VALUES (?, ?, 'event', ?, ?, ?)
        """,
        (ts, day_s, int(mood), tags_s, note_s),
    )
    conn.commit()


def delete_entry(entry_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM entries WHERE id = ?", (int(entry_id),))
    conn.commit()


def clear_all_entries():
    init_db()
    conn = get_conn()
    conn.execute("DELETE FROM entries")
    conn.commit()


def load_data() -> pd.DataFrame:
    init_db()
    conn = get_conn()
    df = pd.read_sql_query(
        """
        SELECT id, timestamp, day, entry_type, mood, tags, note
        FROM entries
        ORDER BY timestamp ASC
        """,
        conn,
    )
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["day"] = pd.to_datetime(df["day"], errors="coerce").dt.date
    df["mood"] = pd.to_numeric(df["mood"], errors="coerce")
    df["tags"] = df["tags"].fillna("")
    df["note"] = df["note"].fillna("")
    df["entry_type"] = df["entry_type"].fillna("event")
    df = df.dropna(subset=["day", "mood"])
    df["mood"] = df["mood"].astype(int)
    return df.sort_values("timestamp")


# ----------------------------
# App analytics events (optional)
# ----------------------------
def ensure_events_csv():
    if not os.path.exists(EVENTS_FILE):
        with open(EVENTS_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp", "event"])


def log_event(event_name: str):
    ensure_events_csv()
    with open(EVENTS_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([now_iso(), event_name])


# ----------------------------
# AI helper
# ----------------------------
def call_ai(prompt: str) -> str:
    if not client:
        return "⚠️ No OpenAI API key found. Please check your .env file."
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a neurodivergent-affirming reflection assistant. "
                        "Be kind, concrete, and non-judgmental. Do not diagnose. "
                        "Avoid shame or blame. Use simple, natural language."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ AI request failed: {e}"


# ----------------------------
# Clipboard helper (copy buttons)
# ----------------------------
def copy_button(label: str, text: str, key: str):
    safe_text = (text or "").replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    html = f"""
    <div style="display:flex; gap:10px; align-items:center; margin:6px 0;">
      <button id="btn_{key}" style="
        padding:8px 12px; border-radius:10px; border:1px solid #ddd;
        background:white; cursor:pointer;
      ">{label}</button>
      <span id="msg_{key}" style="font-size:12px; color:#555;"></span>
    </div>
    <script>
      const btn = document.getElementById("btn_{key}");
      const msg = document.getElementById("msg_{key}");
      btn.addEventListener("click", async () => {{
        try {{
          await navigator.clipboard.writeText(`{safe_text}`);
          msg.textContent = "Copied.";
          setTimeout(()=>msg.textContent="", 1200);
        }} catch (e) {{
          msg.textContent = "Couldn’t copy (browser blocked).";
          setTimeout(()=>msg.textContent="", 2000);
        }}
      }});
    </script>
    """
    components.html(html, height=55)


# ----------------------------
# Metrics helpers
# ----------------------------
def compute_daily_streak(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    daily = df[df["entry_type"] == "daily"].copy()
    if daily.empty:
        return 0
    days = sorted(set(daily["day"]))
    day_set = set(days)

    today = datetime.date.today()
    start_day = today if today in day_set else (today - datetime.timedelta(days=1))
    if start_day not in day_set:
        return 0

    streak = 0
    d = start_day
    while d in day_set:
        streak += 1
        d -= datetime.timedelta(days=1)
    return streak

def compute_daily_streak_in_range(df_range: pd.DataFrame, start_day: datetime.date, end_day: datetime.date) -> int:
    """
    Counts consecutive DAILY check-ins ending at end_day, but only within [start_day, end_day].
    Example: if daily check-ins exist on end_day, end_day-1, end_day-2, streak=3.
    """
    if df_range.empty:
        return 0

    daily = df_range[df_range["entry_type"] == "daily"].copy()
    if daily.empty:
        return 0

    day_set = set(daily["day"])
    if end_day not in day_set:
        return 0

    streak = 0
    d = end_day
    while d >= start_day and d in day_set:
        streak += 1
        d -= datetime.timedelta(days=1)

    return streak

def last_n_days_df(df: pd.DataFrame, n: int = 7) -> pd.DataFrame:
    if df.empty:
        return df
    today = datetime.date.today()
    start = today - datetime.timedelta(days=n - 1)
    return df[df["day"] >= start].copy()


def last_week_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    today = datetime.date.today()
    this_week_start = today - datetime.timedelta(days=today.weekday())
    last_week_start = this_week_start - datetime.timedelta(days=7)
    last_week_end = this_week_start
    return df[(df["day"] >= last_week_start) & (df["day"] < last_week_end)].copy()


def engaged_days_last_week(df: pd.DataFrame) -> int:
    lw = last_week_df(df)
    return lw["day"].nunique() if not lw.empty else 0


def mood_rhythm_last_7_days(df: pd.DataFrame) -> float:
    """
    Mood rhythm = consistency based on DAILY check-in only.
    Higher = more consistent day-to-day; lower = more ups/downs.
    """
    if df.empty:
        return 0.0
    daily = df[df["entry_type"] == "daily"]
    last7 = last_n_days_df(daily, 7)
    if last7.empty:
        return 0.0
    std = float(last7["mood"].std(ddof=0)) if len(last7) > 1 else 0.0
    score = 10.0 - std
    return round(max(0.0, min(10.0, score)), 1)


def week_over_week_mood_change_daily(df: pd.DataFrame):
    if df.empty:
        return None, None, None
    daily = df[df["entry_type"] == "daily"].copy()
    if daily.empty:
        return None, None, None

    today = datetime.date.today()
    this_week_start = today - datetime.timedelta(days=today.weekday())
    last_week_start = this_week_start - datetime.timedelta(days=7)
    last_week_end = this_week_start

    this_week = daily[daily["day"] >= this_week_start]
    last_week = daily[(daily["day"] >= last_week_start) & (daily["day"] < last_week_end)]

    if this_week.empty or last_week.empty:
        return None, None, None

    this_avg = round(float(this_week["mood"].mean()), 1)
    last_avg = round(float(last_week["mood"].mean()), 1)
    delta = round(this_avg - last_avg, 1)
    return last_avg, this_avg, delta


def parse_tags_str(tags: str) -> list[str]:
    if not isinstance(tags, str) or not tags.strip():
        return []
    return [t.strip().lower() for t in tags.split(",") if t.strip()]


def classify_emotion_bucket(tags_str: str, note: str) -> str:
    tags = parse_tags_str(tags_str)
    cats = set()
    for t in tags:
        if t in TAG_CATEGORY:
            cats.add(TAG_CATEGORY[t])

    if "positive" in cats and "negative" not in cats:
        return "positive"
    if "negative" in cats and "positive" not in cats:
        return "negative"
    if cats:
        return "neutral"

    text = (note or "").lower()
    pos = any(w in text for w in ["good", "better", "calm", "win", "relaxed", "focused", "steady"])
    neg = any(w in text for w in ["tired", "overwhelmed", "anxious", "stress", "sensory", "meltdown", "shutdown"])
    if pos and not neg:
        return "positive"
    if neg and not pos:
        return "negative"
    return "neutral"


def top_themes_from_tags(df: pd.DataFrame, top_n: int = 8) -> list[str]:
    if df.empty:
        return []
    all_tags = []
    for t in df["tags"].fillna("").tolist():
        all_tags.extend(parse_tags_str(t))
    c = Counter(all_tags)
    return [t for t, _ in c.most_common(top_n)]


def attention_items_for_week(df: pd.DataFrame) -> list[str]:
    items = []
    lw = last_week_df(df)
    last7 = last_n_days_df(df, 7)

    engaged = lw["day"].nunique() if not lw.empty else 0
    if engaged <= 2:
        items.append("Fewer check-ins last week (2 days or less).")

    if not last7.empty:
        ev = last7[last7["entry_type"] == "event"]
        if not ev.empty:
            counts = ev.groupby("day").size().sort_values(ascending=False)
            top_day = counts.index[0]
            top_count = int(counts.iloc[0])
            if top_count >= 3:
                items.append(f"One day had several event check-ins ({top_count} on {top_day}).")

    def count_like(keys):
        total = 0
        for _, row in last7.iterrows():
            tags = (row["tags"] or "").lower()
            note = (row["note"] or "").lower()
            if any(k in tags or k in note for k in keys):
                total += 1
        return total

    sleep_mentions = count_like(["sleep"])
    if sleep_mentions >= 2:
        items.append(f"Sleep came up multiple times this week ({sleep_mentions}).")

    sensory_mentions = count_like(["sensory", "overload"])
    if sensory_mentions >= 2:
        items.append(f"Sensory load came up multiple times this week ({sensory_mentions}).")

    shutdown_mentions = count_like(["shutdown", "meltdown"])
    if shutdown_mentions >= 2:
        items.append(f"Shutdown/meltdown themes appeared more than once ({shutdown_mentions}).")

    return items


def make_date_range_df(start_day: datetime.date, end_day: datetime.date) -> pd.DataFrame:
    days = []
    d = start_day
    while d <= end_day:
        days.append(d)
        d += datetime.timedelta(days=1)
    return pd.DataFrame({"day": days})


# ----------------------------
# Demo seed (varied + events)
# ----------------------------
def seed_demo_if_empty(days: int = 180, seed: int = 42):
    init_db()
    conn = get_conn()
    count = conn.execute("SELECT COUNT(1) FROM entries").fetchone()[0]
    if count > 0:
        return

    random.seed(seed)
    today = datetime.date.today()

    NOTE_SNIPPETS = {
        "sleep issues": ["Sleep felt off; needed extra recovery time.", "Woke up a few times; felt foggy."],
        "work stress": ["A lot on my plate; took small breaks to reset.", "Many tasks; tried to do one thing at a time."],
        "sensory overload": ["Noise/light felt intense; stepped away.", "Noticed sensory sensitivity; needed quiet."],
        "therapy win": ["Tried a therapy skill; it helped a bit.", "Named the feeling without judging it."],
        "self-care": ["Did one small self-care thing.", "Paused and checked what I needed."],
        "focus/flow": ["Had a brief focus window and got something done.", "Felt more focused than usual for a bit."],
        "routine change": ["Schedule changed; took time to adjust.", "Unexpected change; recovered after a break."],
        "anxiety": ["Worry showed up; grounding helped a little.", "Tried to stay present when anxiety rose."],
        "exercise/movement": ["Movement helped settle my body.", "A short walk helped a bit."],
        "social interaction": ["Social time took energy; recovery helped.", "Conversation was okay but tiring."],
        "quiet time": ["Quiet time helped me reset.", "A break in a calm space helped."],
    }

    for i in range(days):
        day = today - datetime.timedelta(days=i)
        weekday = day.weekday()
        is_weekend = weekday >= 5

        weekly_wave = math.sin((i / 7.0) * 2 * math.pi)
        base = 6.2 + 0.7 * weekly_wave + (0.5 if is_weekend else 0.0)

        sensory_spike = (random.random() < (0.10 if not is_weekend else 0.06))
        routine_change = (random.random() < 0.09)
        therapy_day = (weekday == 2 and random.random() < 0.75)
        exercise_day = (random.random() < (0.35 if is_weekend else 0.20))
        social_day = (random.random() < (0.22 if is_weekend else 0.14))
        sleep_issue = (random.random() < 0.14)

        mood = base
        tags = set()

        if sleep_issue:
            mood -= random.uniform(0.6, 1.2)
            tags.add("sleep issues")
        if sensory_spike:
            mood -= random.uniform(0.8, 1.8)
            tags.update(["sensory overload", "quiet time"])
        if routine_change:
            mood -= random.uniform(0.3, 1.1)
            tags.add("routine change")
        if therapy_day:
            mood += random.uniform(0.2, 0.9)
            tags.add("therapy win")
        if exercise_day:
            mood += random.uniform(0.2, 0.7)
            tags.add("exercise/movement")
        if social_day:
            tags.add("social interaction")
            if random.random() < 0.35:
                mood -= random.uniform(0.2, 0.8)

        mood_int = int(round(max(1.0, min(10.0, mood))))

        if mood_int <= 4:
            tags.update(["fatigue", "anxiety"])
            if random.random() < 0.25:
                tags.add("meltdown/shutdown")
        elif mood_int >= 8:
            tags.update(["focus/flow", "self-care"])

        while len(tags) < random.randint(2, 4):
            tags.add(random.choice(SUGGESTED_TAGS))

        tags_list = sorted(tags)

        note = ""
        if random.random() < 0.65:
            candidates = [t for t in tags_list if t in NOTE_SNIPPETS]
            if candidates:
                note = random.choice(NOTE_SNIPPETS[random.choice(candidates)])

        save_daily_checkin(day=day, mood=mood_int, tags=tags_list, note=note)

        # events: allow both positive + negative
        if mood_int <= 4:
            event_count = random.choice([1, 2, 2, 3])
        elif mood_int >= 8:
            event_count = random.choice([0, 1, 1, 2])
        else:
            event_count = random.choice([0, 1, 1, 2])

        for _ in range(event_count):
            if random.random() < 0.5:
                event_tags = random.choice([["anxiety"], ["work stress"], ["sensory overload"], ["fatigue"], ["quiet time"]])
                event_mood = max(1, min(10, mood_int + random.choice([-2, -1, 0, 0, 1])))
                event_note = random.choice(["Noticed a spike; took a pause.", "Needed lower stimulation.", "Quick check-in.", ""])
            else:
                event_tags = random.choice([["self-care"], ["focus/flow"], ["therapy win"], ["exercise/movement"], ["quiet time"]])
                event_mood = max(1, min(10, mood_int + random.choice([0, 0, 1, 2])))
                event_note = random.choice(["A small win.", "Felt more steady for a bit.", "Quick check-in.", ""])

            fake_dt = datetime.datetime.combine(day, datetime.time(9, 0)) + datetime.timedelta(
                hours=random.randint(0, 12),
                minutes=random.randint(0, 59),
            )

            conn = get_conn()
            conn.execute(
                """
                INSERT INTO entries (timestamp, day, entry_type, mood, tags, note)
                VALUES (?, ?, 'event', ?, ?, ?)
                """,
                (fake_dt.isoformat(timespec="seconds"), day.isoformat(), int(event_mood), ", ".join(event_tags), event_note[:500]),
            )
            conn.commit()


# ----------------------------
# PDF builders
# ----------------------------
def build_clinician_pdf(
    client_name: str,
    week_range: str,
    metrics: dict,
    top_themes: list,
    attention_items: list,
    snapshot_text: str,
    details_text: str,
) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 50

    def draw_line(text, size=11, gap=16, bold=False):
        nonlocal y
        if y < 70:
            c.showPage()
            y = height - 50
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(50, y, (text or "")[:110])
        y -= gap

    draw_line("RevoraAI — Clinician Session Prep Snapshot", size=14, gap=22, bold=True)
    draw_line(f"Client: {client_name}", bold=True)
    draw_line(f"Week: {week_range}")
    draw_line(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 10

    draw_line("Weekly snapshot", bold=True)
    for k, v in metrics.items():
        draw_line(f"- {k}: {v}")
    y -= 10

    draw_line("Top themes", bold=True)
    draw_line(", ".join(top_themes) if top_themes else "None yet.")
    y -= 10

    draw_line("Things to notice", bold=True)
    if attention_items:
        for item in attention_items:
            draw_line(f"- {item}")
    else:
        draw_line("None flagged.")
    y -= 10

    draw_line("Session prep", bold=True)
    if snapshot_text.strip():
        for line in snapshot_text.splitlines():
            line = line.strip()
            if line:
                draw_line(line, size=10, gap=14)
    else:
        draw_line("Not provided.")
    y -= 10

    draw_line("Prep notes + Conversation starters", bold=True)
    if details_text.strip():
        for line in details_text.splitlines():
            line = line.strip()
            if line:
                draw_line(line, size=10, gap=14)
    else:
        draw_line("Not provided.")

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def dashboard_image_png_bytes(daily_mood_df: pd.DataFrame, daily_counts_df: pd.DataFrame, metrics_text: str) -> bytes:
    """
    Creates a single PNG image with:
      - metrics text header
      - mood trend line
      - engagement bars (total entries/day)
    """
    fig = plt.figure(figsize=(11, 7))
    fig.patch.set_facecolor("white")

    gs = fig.add_gridspec(3, 1, height_ratios=[0.6, 1.2, 1.2])

    ax0 = fig.add_subplot(gs[0, 0])
    ax0.axis("off")
    ax0.text(0.01, 0.75, "RevoraAI — Dashboard Export", fontsize=14, fontweight="bold")
    ax0.text(0.01, 0.25, metrics_text, fontsize=10)

    ax1 = fig.add_subplot(gs[1, 0])
    ax1.set_title("Mood trend")
    if not daily_mood_df.empty:
        ax1.plot(daily_mood_df["day_dt"], daily_mood_df["mood"], marker="o")
    ax1.set_ylim(1, 10)
    ax1.set_ylabel("Mood (1–10)")
    ax1.grid(True, alpha=0.25)

    ax2 = fig.add_subplot(gs[2, 0])
    ax2.set_title("Engagement trend")
    if not daily_counts_df.empty:
        ax2.bar(daily_counts_df["day_dt"], daily_counts_df["entries_total"])
    ax2.set_ylabel("Entries")
    ax2.grid(True, axis="y", alpha=0.25)

    fig.autofmt_xdate(rotation=25)

    buf = BytesIO()
    plt.tight_layout()
    fig.savefig(buf, format="png", dpi=160)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_dashboard_pdf(metrics_text: str, png_bytes: bytes) -> bytes:
    """
    Simple PDF: title + one embedded PNG (the dashboard image).
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, height - 50, "RevoraAI — Dashboard Export")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 70, metrics_text[:120])

    img = ImageReader(BytesIO(png_bytes))
    img_w = width - 100
    img_h = height - 130
    c.drawImage(img, 50, 60, width=img_w, height=img_h, preserveAspectRatio=True, anchor="c")

    c.showPage()
    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


# ----------------------------
# Dashboard period helpers
# ----------------------------
def week_start(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=d.weekday())


def month_start(d: datetime.date) -> datetime.date:
    return datetime.date(d.year, d.month, 1)


def year_start(d: datetime.date) -> datetime.date:
    return datetime.date(d.year, 1, 1)


def add_months(d: datetime.date, n: int) -> datetime.date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return datetime.date(y, m, 1)


def build_periods(df: pd.DataFrame, granularity: str) -> list[tuple[datetime.date, datetime.date, str]]:
    """
    Returns list of periods: (start_day, end_exclusive, label), sorted ascending by start_day.
    """
    if df.empty:
        return []

    days = sorted(set(df["day"]))
    if not days:
        return []

    min_day, max_day = min(days), max(days)

    periods = []
    if granularity == "Weekly":
        start = week_start(min_day)
        end_limit = week_start(max_day) + datetime.timedelta(days=7)
        while start < end_limit:
            end = start + datetime.timedelta(days=7)
            label = f"Week of {start.strftime('%b %d, %Y')}"
            periods.append((start, end, label))
            start = end

    elif granularity == "Monthly":
        start = month_start(min_day)
        end_limit = add_months(month_start(max_day), 1)
        while start < end_limit:
            end = add_months(start, 1)
            label = start.strftime("%B %Y")
            periods.append((start, end, label))
            start = end

    else:  # Yearly
        start = year_start(min_day)
        end_limit = datetime.date(year_start(max_day).year + 1, 1, 1)
        while start < end_limit:
            end = datetime.date(start.year + 1, 1, 1)
            label = str(start.year)
            periods.append((start, end, label))
            start = end

    return periods


def clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


# ----------------------------
# Seed + load
# ----------------------------
seed_demo_if_empty(days=180, seed=42)
df = load_data()


# ----------------------------
# Sidebar navigation
# ----------------------------
st.sidebar.title("RevoraAI 🪞")
st.sidebar.caption("Self Reflection & Clinician Insight Prototype")

page = st.sidebar.radio(
    "Navigation",
    ["Check-In", "Clinician Summary", "Dashboard", "About"],
)

st.sidebar.divider()
st.sidebar.subheader("Demo controls")

if st.sidebar.button("Reset demo data"):
    clear_all_entries()
    seed_demo_if_empty(days=180, seed=42)
    st.sidebar.success("Demo data reset.")
    st.rerun()

st.divider()


# ----------------------------
# Session state
# ----------------------------
if "snapshot_edit" not in st.session_state:
    st.session_state["snapshot_edit"] = ""

if "details_edit" not in st.session_state:
    st.session_state["details_edit"] = ""

if "dash_granularity" not in st.session_state:
    st.session_state["dash_granularity"] = "Weekly"

if "dash_period_idx" not in st.session_state:
    st.session_state["dash_period_idx"] = 0

# ----------------------------
# Dashboard Motivation Banner
# ----------------------------
def render_dashboard_banner(avg_mood: float | None):
    """
    Renders a styled motivational banner at the top of Dashboard.
    """

    if avg_mood is None:
        message = "Showing up matters. One small check-in is enough for today."
        bg = "linear-gradient(135deg, #eef2ff, #f8fafc)"
    elif avg_mood <= 3.5:
        message = "Tough stretch. Be gentle with yourself — small steps still count."
        bg = "linear-gradient(135deg, #ffe4e6, #fef3f2)"
    elif avg_mood <= 5.5:
        message = "Steady progress. You’re doing more than you think."
        bg = "linear-gradient(135deg, #e0f2fe, #f0f9ff)"
    elif avg_mood <= 7.5:
        message = "Nice momentum. Keep building on what’s working."
        bg = "linear-gradient(135deg, #e0f7ec, #f0fdf4)"
    else:
        message = "You’re in a strong place. Protect this rhythm."
        bg = "linear-gradient(135deg, #ede9fe, #f5f3ff)"

    st.markdown(
        f"""
        <div style="
            padding:22px 26px;
            border-radius:18px;
            background:{bg};
            border:1px solid rgba(0,0,0,0.05);
            box-shadow: 0 8px 20px rgba(0,0,0,0.04);
            margin-bottom:22px;
        ">
            <div style="font-size:14px; color:#555; letter-spacing:0.5px;">
                Reflection Insight
            </div>
            <div style="font-size:20px; font-weight:600; margin-top:6px;">
                {message}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ----------------------------
# Pages
# ----------------------------
if page == "Check-In":
    st.header("Daily Check-In")

    if os.path.exists("header.webp"):
        st.image("header.webp", use_container_width=True)

    st.markdown(
        """
        <div style="padding:16px;border-radius:14px;background:#f6f7fb;">
        <b>Quick daily check-in.</b><br>
         Log how you feel in 10 seconds and add event check-ins anytime something shifts. Generate a weekly summary when you want it.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

    # Always refresh data for this page (so recent table + metrics update immediately)
    df = load_data()
    today = datetime.date.today()

    # --- Helpers (local)
    def mood_emoji(x: int) -> str:
        if x <= 2:
            return "😞"
        elif x <= 4:
            return "😕"
        elif x <= 6:
            return "😐"
        elif x <= 8:
            return "🙂"
        else:
            return "😄"

    SAVE_MESSAGES = [
        "Saved. Small steps count, keep going.",
        "Saved. Thanks for checking in. You did enough for today.",
        "Saved. Progress over perfection.",
        "Saved. Be gentle with yourself today.",
        "Saved. One small check-in can help.",
    ]

    if "save_message_daily" not in st.session_state:
        st.session_state["save_message_daily"] = ""
    if "save_message_event" not in st.session_state:
        st.session_state["save_message_event"] = ""

    tab_daily, tab_event = st.tabs(["Daily check-in (once/day)", "Event check-in (anytime)"])

    # ----------------------------
    # DAILY
    # ----------------------------
    with tab_daily:
        st.subheader("Daily check-in")
        st.caption("A simple daily anchor. Saving again today updates today’s daily check-in.")

        e1, e2 = st.columns([1, 10])
        with e1:
            st.markdown("😔")
        with e2:
            st.markdown("<div style='text-align:right;'>😊</div>", unsafe_allow_html=True)

        mood = st.slider("How are you feeling today?", 1, 10, 5, key="daily_mood")
        st.markdown(f"### {mood_emoji(int(mood))}  **{int(mood)}/10**")

        selected = st.multiselect("Tags (optional)", SUGGESTED_TAGS, key="daily_tags")

        st.caption("⚠️ Public demo — please don’t enter personal or identifying information.")
        note_free = st.text_area("Optional note", "", max_chars=500, height=90, key="daily_note")

        if st.button("Save", key="save_daily"):
            save_daily_checkin(today, mood, selected, note_free.strip())
            log_event("daily_saved")
            st.session_state["save_message_daily"] = random.choice(SAVE_MESSAGES)
            st.rerun()

        if st.session_state.get("save_message_daily"):
            st.success(st.session_state["save_message_daily"])

    # ----------------------------
    # EVENT
    # ----------------------------
    with tab_event:
        st.subheader("Event check-in")
        st.caption("Use this when something changes — good, bad, or neutral. Multiple per day is fine.")

        e1, e2 = st.columns([1, 10])
        with e1:
            st.markdown("😔")
        with e2:
            st.markdown("<div style='text-align:right;'>😊</div>", unsafe_allow_html=True)

        event_mood = st.slider("How are you feeling right now?", 1, 10, 5, key="event_mood")
        st.markdown(f"### {mood_emoji(int(event_mood))}  **{int(event_mood)}/10**")

        event_tags = st.multiselect("Tags (optional)", SUGGESTED_TAGS, key="event_tags")

        st.caption("⚠️ Public demo — please don’t enter personal or identifying information.")
        event_note = st.text_area("Optional note", "", max_chars=500, height=90, key="event_note")

        if st.button("Save", key="save_event"):
            save_event_checkin(event_mood, event_tags, event_note.strip())
            log_event("event_saved")
            st.session_state["save_message_event"] = random.choice(SAVE_MESSAGES)
            st.rerun()

        if st.session_state.get("save_message_event"):
            st.success(st.session_state["save_message_event"])

    # ----------------------------
    # METRICS (TOTALS, NO ACTIVE DAYS)
    # ----------------------------
    st.divider()

    df = load_data()

    daily_streak = compute_daily_streak(df)
    total_entries = len(df) if not df.empty else 0
    total_event_entries = int((df["entry_type"] == "event").sum()) if not df.empty else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Reflection streak", f"{daily_streak} days")
    c2.metric("Total event check-ins", total_event_entries)
    c3.metric("Total entries", total_entries)

    # ----------------------------
    # RECENT ENTRIES
    # ----------------------------
    st.subheader("Recent entries")

    if df.empty:
        st.caption("No entries yet.")
    else:
        recent = df.sort_values("timestamp", ascending=False).head(12).copy()
        recent["time"] = recent["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
        recent["type"] = recent["entry_type"].map({"daily": "Daily check-in", "event": "Event"})
        view = recent[["id", "time", "type", "mood", "tags", "note"]].copy()
        view.insert(0, "Delete", False)

        edited = st.data_editor(
            view,
            use_container_width=True,
            hide_index=True,
            disabled=["id", "time", "type", "mood", "tags", "note"],
            column_config={
                "id": None  # hides the id column completely
            }
        )
        to_delete = edited[edited["Delete"] == True]
        if not to_delete.empty:
            if st.button("Delete selected"):
                for entry_id in to_delete["id"].tolist():
                    delete_entry(entry_id)
                st.success("Deleted.")
                st.rerun()

    # ----------------------------
    # GENERATE SUMMARY (BOTTOM BUTTON)
    # ----------------------------
    st.divider()
    st.subheader("Weekly Summary")
    st.caption("A gentle recap from your last 7 days.")

    if "user_weekly_summary" not in st.session_state:
        st.session_state["user_weekly_summary"] = ""

    last7_all = last_n_days_df(df, 7)

    colA, colB = st.columns([1, 1])
    make_user_summary = colA.button("Generate summary ✨", key="gen_user_weekly")
    clear_user_summary = colB.button("Clear", key="clear_user_weekly")

    def entry_line_user(row):
        ts = row["timestamp"].strftime("%Y-%m-%d %H:%M")
        et = "daily check-in" if row["entry_type"] == "daily" else "event"
        tags = row["tags"] or ""
        note = row["note"] or ""
        return f"- {ts} ({et}): mood={int(row['mood'])}, tags=[{tags}], note={note}"

    entries_text = "\n".join(
        [entry_line_user(r) for _, r in last7_all.sort_values("timestamp").iterrows()]
    )

    if clear_user_summary:
        st.session_state["user_weekly_summary"] = ""

    if make_user_summary:
        if last7_all.empty:
            st.session_state["user_weekly_summary"] = "Add a few check-ins first, then generate a summary."
        else:
            prompt = f"""
You are a supportive reflection assistant.

Write a recap in simple, natural language:
- 2–4 sentence summary
- What came up most (max 3 bullets)
- What helped (max 2 bullets)
- One gentle next step (1 bullet)

Avoid judgmental language. Do not diagnose.

Entries (last 7 days, includes daily check-in + events):
{entries_text}
"""
            st.session_state["user_weekly_summary"] = call_ai(prompt)

    if st.session_state.get("user_weekly_summary"):
        st.success(st.session_state["user_weekly_summary"])
        copy_button("Copy summary", st.session_state["user_weekly_summary"], key="copy_user_summary")
elif page == "Clinician Summary":
    st.header("Clinician Summary")

    df = load_data()

    if df.empty:
        st.info("Add check-ins first.")
    else:
        # ----------------------------
        # Date filter (placed under header)
        # ----------------------------
        st.markdown("<br>", unsafe_allow_html=True)

        min_day = df["day"].min()
        max_day = df["day"].max()

        # default: last 7 days available (bounded by data)
        default_end = max_day
        default_start = max(min_day, max_day - datetime.timedelta(days=6))

        cF1, cF2 = st.columns(2)
        with cF1:
            start_day = st.date_input(
                "From", value=default_start, min_value=min_day, max_value=max_day
            )
        with cF2:
            end_day = st.date_input(
                "To", value=default_end, min_value=min_day, max_value=max_day
            )

        # normalize if user picks reversed dates
        if start_day > end_day:
            start_day, end_day = end_day, start_day

        # inclusive filter
        df_view = df[(df["day"] >= start_day) & (df["day"] <= end_day)].copy()

        st.markdown("<br>", unsafe_allow_html=True)

        if df_view.empty:
            st.warning("No entries in this date range.")
            st.stop()

        # last 7 days inside the selected range (anchored to end_day)
        last7_start = max(start_day, end_day - datetime.timedelta(days=6))
        last7_all = df_view[(df_view["day"] >= last7_start) & (df_view["day"] <= end_day)].copy()
        last7_daily = last7_all[last7_all["entry_type"] == "daily"].copy()
        # ----------------------------
        # Metrics based on filtered data (ACCURATE, SELECTED RANGE ONLY)
        # ----------------------------
        range_days = (end_day - start_day).days + 1

        # Split daily vs event inside selected range
        daily_df = df_view[df_view["entry_type"] == "daily"].copy()
        event_df = df_view[df_view["entry_type"] == "event"].copy()

        # Days with any entry (daily OR event)
        engaged_days = df_view["day"].nunique()

        # Daily check-in coverage (how many days have the daily anchor)
        daily_days = daily_df["day"].nunique()

        # Event volume + event days
        event_count_in_range = len(event_df)
        event_days = event_df["day"].nunique()

        # Average mood: choose what you want to reflect
        # Clinician-facing: daily mood is usually the cleanest anchor
        avg_daily_mood = round(float(daily_df["mood"].mean()), 1) if not daily_df.empty else 0.0

        # Optional: overall average across ALL entries (daily+event) – keep if you want
        avg_all_mood = round(float(df_view["mood"].mean()), 1) if not df_view.empty else 0.0

        # Volatility (daily-only): average day-to-day absolute change
        if len(daily_df) >= 2:
            daily_sorted = daily_df.sort_values("day")
            diffs = daily_sorted["mood"].diff().abs().dropna()
            avg_jump = float(diffs.mean()) if not diffs.empty else 0.0
        else:
            avg_jump = 0.0

        # Turn volatility into a 0–10 “steadiness” score (higher = steadier)
        # (This is more sensitive than std and changes more across ranges)
        steadiness = round(max(0.0, min(10.0, 10.0 - (avg_jump * 2.0))), 1)

        # Event density: events per engaged day (avoid divide-by-zero)
        events_per_engaged_day = round(event_count_in_range / engaged_days, 2) if engaged_days else 0.0

        # Daily streak within selected range, ending at the LAST daily day <= end_day
        # (More intuitive than “0” when end_day has no daily check-in)
        def compute_daily_streak_in_range_anchor(df_daily: pd.DataFrame, start_day: datetime.date, end_day: datetime.date) -> int:
            if df_daily.empty:
                return 0
            day_set = set(df_daily["day"])
 
            # Anchor to last available daily day <= end_day
            anchor = max([d for d in day_set if d <= end_day], default=None)
            if anchor is None or anchor < start_day:
                return 0

            streak = 0
            d = anchor
            while d >= start_day and d in day_set:
                streak += 1
                d -= datetime.timedelta(days=1)
            return streak

        daily_streak = compute_daily_streak_in_range_anchor(daily_df, start_day, end_day)

        # ----------------------------
        # Tile display (clinician-oriented, non-duplicative)
        # ----------------------------
        st.subheader(f"Snapshot ({start_day} → {end_day})")
        st.caption("All metrics below reflect only the selected date range.")
        st.markdown("<br>", unsafe_allow_html=True)

        s1, s2, s3, s4, s5 = st.columns(5)

        s1.metric(
             "Daily coverage",
             f"{daily_days}/{range_days}",
             help="How many days in the selected range have a daily check-in."
        )

        s2.metric(
             "Engaged days",
             f"{engaged_days}/{range_days}",
             help="Days with at least one entry (daily or event) in the selected range."
        )

        s3.metric(
             "Avg daily mood",
             avg_daily_mood,
             help="Average mood in the selected date range."
        )

        s4.metric(
             "Steadiness",
             f"{steadiness}/10",
             help="Based on average day-to-day mood change. Higher = steadier."
        )

        s5.metric(
             "Events",
             f"{event_count_in_range}",
             help="Total event check-ins in the selected range."
        )

        st.divider()

        # ----------------------------
        # Trend callout (first half vs second half of selected range)
        # ----------------------------
        daily_view = df_view[df_view["entry_type"] == "daily"].copy()
        range_days = (end_day - start_day).days + 1

        if range_days < 2:
            st.info("Trend: Pick at least 2 days to see a comparison.")
        else:
            mid_offset = range_days // 2  # integer split
            mid_day = start_day + datetime.timedelta(days=mid_offset - 1)  # end of first half
            second_half_start = mid_day + datetime.timedelta(days=1)

            first_half = daily_view[(daily_view["day"] >= start_day) & (daily_view["day"] <= mid_day)]
            second_half = daily_view[(daily_view["day"] >= second_half_start) & (daily_view["day"] <= end_day)]

            if first_half.empty or second_half.empty:
                st.info("Trend: Add daily check-ins in both halves of this date range to see a comparison.")
            else:
                first_avg = round(float(first_half["mood"].mean()), 1)
                second_avg = round(float(second_half["mood"].mean()), 1)
                delta = round(second_avg - first_avg, 1)

                if delta > 0:
                    headline = f"Average mood is up by {abs(delta)}"
                    detail = f"Second half: {second_avg} vs first half: {first_avg}."
                elif delta < 0:
                    headline = f"Average mood is down by {abs(delta)}"
                    detail = f"Second half: {second_avg} vs first half: {first_avg}."
                else:
                    headline = "Average mood is steady"
                    detail = f"Both halves are around {second_avg}."

                st.markdown(
                    f"""
                    <div style="
                        padding:14px 16px;
                        border-radius:14px;
                        background:#f6f7fb;
                        border:1px solid #e7e9f2;
                        margin-top:6px;
                        margin-bottom:6px;
                    ">
                      <div style="font-size:20px; font-weight:700;">
                        {headline}
                      </div>
                      <div style="font-size:14px; margin-top:6px;">
                        {detail}
                      </div>
                      <div style="font-size:12px; margin-top:6px; color:#666;">
                        (Compared within the selected date range)
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.divider()

        # ----------------------------
        # Top themes (from filtered last 7)
        # ----------------------------
        st.subheader("Top themes")
        top_tags = top_themes_from_tags(df_view, top_n=8)
        if top_tags:
            st.markdown(" ".join([f"`{t}`" for t in top_tags]))
        else:
            st.caption("No themes yet.")

        st.divider()

        # ----------------------------
        # Things to notice (uses helper on df_view)
        # ----------------------------
        st.subheader("Things to notice")
        attention_items = attention_items_for_range(df_view, start_day, end_day)
        if attention_items:
            for item in attention_items:
                st.write(f"• {item}")
        else:
            st.caption("No items flagged.")

        st.divider()

        # ----------------------------
        # Emotions tracker (on last 7 within filter)
        # ----------------------------
        st.subheader("Emotions tracker")
        st.caption("A light grouping into positive / neutral / negative based on tags + simple keywords.")

        tmp = df_view.copy()
        tmp["bucket"] = tmp.apply(lambda r: classify_emotion_bucket(r["tags"], r["note"]), axis=1)

        counts = (
            tmp["bucket"]
            .value_counts()
            .reindex(["positive", "neutral", "negative"], fill_value=0)
            .reset_index()
        )
        counts.columns = ["bucket", "count"]

        color_scale = alt.Scale(
            domain=["negative", "neutral", "positive"],
            range=["#D64545", "#8EC5FF", "#2F6BFF"],
        )

        chart = (
            alt.Chart(counts)
            .mark_bar()
            .encode(
                x=alt.X("bucket:N", title=""),
                y=alt.Y("count:Q", title="Count"),
                color=alt.Color("bucket:N", scale=color_scale, legend=None),
                tooltip=["bucket:N", "count:Q"],
            )
            .properties(height=260)
        )
        st.altair_chart(chart, use_container_width=True)

        st.divider()

        # ----------------------------
        # AI session prep (uses FULL selected date range, but compressed)
        # ----------------------------
        st.subheader("AI session prep")

        colA, colB, colC = st.columns([1, 1, 1])
        make_snapshot = colA.button("Generate summary ✨")
        make_details = colB.button("Prep notes + Conversation starters")
        clear_outputs = colC.button("Clear")

        if clear_outputs:
            st.session_state["snapshot_edit"] = ""
            st.session_state["details_edit"] = ""

        # Build a compact per-day summary so long ranges don't explode the prompt
        def build_compact_entries_text(df_in: pd.DataFrame) -> str:
            if df_in.empty:
                return ""

            lines = []
            df_sorted = df_in.sort_values(["day", "timestamp"]).copy()

            for day, g in df_sorted.groupby("day"):
                daily = g[g["entry_type"] == "daily"].sort_values("timestamp")
                events = g[g["entry_type"] == "event"].sort_values("timestamp")

                daily_part = ""
                if not daily.empty:
                    drow = daily.iloc[-1]
                    daily_part = f"daily mood={int(drow['mood'])}"
                    if str(drow.get("tags", "")).strip():
                        daily_part += f", tags=[{drow['tags']}]"
                    if str(drow.get("note", "")).strip():
                        note = str(drow["note"]).strip().replace("\n", " ")
                        daily_part += f", note='{note[:120]}'"

                event_part = ""
                if not events.empty:
                    event_count = len(events)

                    all_event_tags = []
                    for t in events["tags"].fillna("").tolist():
                        all_event_tags.extend([x.strip().lower() for x in str(t).split(",") if x.strip()])
                    top_event_tags = [t for t, _ in Counter(all_event_tags).most_common(4)]
                    tag_txt = f", tags={top_event_tags}" if top_event_tags else ""

                    sample_notes = []
                    for n in events["note"].fillna("").astype(str).tolist():
                        n = n.strip().replace("\n", " ")
                        if n:
                            sample_notes.append(n[:90])
                        if len(sample_notes) >= 2:
                            break
                    note_txt = f", notes={sample_notes}" if sample_notes else ""

                    event_part = f"events={event_count}{tag_txt}{note_txt}"

                if not daily_part and not event_part:
                    continue

                if daily_part and event_part:
                    lines.append(f"- {day}: {daily_part}; {event_part}")
                else:
                    lines.append(f"- {day}: {daily_part or event_part}")

            return "\n".join(lines)

        entries_text = build_compact_entries_text(df_view)
        range_label = f"{start_day} to {end_day}"

        if make_snapshot:
            prompt = f"""
You are helping a clinician prepare for a session with an autistic/neurodivergent adult.

Use the entries for this selected date range: {range_label}.

Write:
- A short natural paragraph summary (2–4 sentences)
- Themes (max 3 bullets)
- Supports that helped (max 2 bullets)
- Suggested focus next session (1 bullet)

Use non-judgmental language. Do not diagnose. Avoid labels like “good/bad patient”.

Entries (compressed daily summary; includes daily check-in + event check-ins):
{entries_text}
"""
            st.session_state["snapshot_edit"] = call_ai(prompt)

        if make_details:
            prompt = f"""
You are helping a clinician prepare for a session with an autistic/neurodivergent adult.

Use the entries for this selected date range: {range_label}.

Generate:
Session prep notes (3 bullets)
Conversation starters (3 bullets)

Keep language simple and non-judgmental. Do not diagnose.

Entries (compressed daily summary; includes daily check-in + event check-ins):
{entries_text}
"""
            st.session_state["details_edit"] = call_ai(prompt)

        # ---- Show outputs only after generation
        if st.session_state.get("snapshot_edit"):
            st.markdown("### Summary")
            st.info(st.session_state["snapshot_edit"])

            with st.expander("Edit summary before export"):
                st.session_state["snapshot_edit"] = st.text_area(
                    "Summary (edit)",
                    value=st.session_state["snapshot_edit"],
                    height=170,
                )
                copy_button("Copy summary", st.session_state["snapshot_edit"], key="copy_snapshot")

        if st.session_state.get("details_edit"):
            st.markdown("### Prep notes + Conversation starters")
            st.success(st.session_state["details_edit"])

            with st.expander("Edit prep notes + Conversation starters before export"):
                st.session_state["details_edit"] = st.text_area(
                    "Prep notes + Conversation starters (edit)",
                    value=st.session_state["details_edit"],
                    height=210,
                )
                copy_button("Copy prep notes", st.session_state["details_edit"], key="copy_details")

        st.divider()

        # ----------------------------
        # Export for clinician (ALWAYS visible)
        # ----------------------------
        st.subheader("Export for clinician")
        client_name = st.text_input("Client name (optional)", value="")

        event_checkins_in_range = int((df_view["entry_type"] == "event").sum())

        metrics = {
            "Daily coverage (selected range)": f"{daily_days}/{range_days}",
            "Engaged days (selected range)": f"{engaged_days}/{range_days}",
            "Avg daily mood (selected range)": f"{avg_daily_mood}",
            "Steadiness (daily-only)": f"{steadiness}/10",
            "Event check-ins (selected range)": f"{event_count_in_range} (≈{events_per_engaged_day}/engaged day)",
            "Daily streak (anchored to last daily in range)": f"{daily_streak} day(s)",
        }
        pdf_bytes = build_clinician_pdf(
            client_name=client_name.strip() if client_name.strip() else "—",
            week_range=f"{start_day} to {end_day}",
            metrics=metrics,
            top_themes=top_tags,
            attention_items=attention_items,
            snapshot_text=st.session_state.get("snapshot_edit", ""),
            details_text=st.session_state.get("details_edit", ""),
        )

        st.download_button(
            label="Download",
            data=pdf_bytes,
            file_name=f"revoraai_clinician_snapshot_{datetime.date.today()}.pdf",
            mime="application/pdf",
        )
elif page == "Dashboard":
    st.header("Dashboard")

    if df.empty:
        st.warning("No data yet. Add check-ins to see trends.")
    else:

        # ----------------------------
        # Date range filter (same UX as Clinician Summary)
        # ----------------------------
        min_day = df["day"].min()
        max_day = df["day"].max()

        # default: last 30 days available (bounded by data)
        default_end = max_day
        default_start = max(min_day, max_day - datetime.timedelta(days=29))

        cF1, cF2 = st.columns(2)
        with cF1:
            start_day = st.date_input("From", value=default_start, min_value=min_day, max_value=max_day, key="dash_from")
        with cF2:
            end_day = st.date_input("To", value=default_end, min_value=min_day, max_value=max_day, key="dash_to")

        if start_day > end_day:
            start_day, end_day = end_day, start_day

        # inclusive filter
        df_view = df[(df["day"] >= start_day) & (df["day"] <= end_day)].copy()

        if df_view.empty:
            st.warning("No entries in this date range.")
            st.stop()

        label = f"{start_day} → {end_day}"
        end_inclusive = end_day
        # ----------------------------
        # METRICS (TOP, ACCURATE FOR SELECTED PERIOD)
        # ----------------------------
        if df_view.empty:
            total_checkins = 0
            active_days = 0
            avg_mood = 0.0
            event_checkins = 0
        else:
            total_checkins = len(df_view)  # daily + event
            active_days = df_view["day"].nunique()
            daily_only = df_view[df_view["entry_type"] == "daily"]
            avg_mood = round(float(daily_only["mood"].mean()), 1) if not daily_only.empty else 0.0
            event_checkins = int((df_view["entry_type"] == "event").sum())

        t1, t2, t3, t4 = st.columns(4)
        t1.metric("Average mood", avg_mood)
        t2.metric("Active days", active_days)
        t3.metric("Total check-ins", total_checkins)
        t4.metric("Event check-ins", event_checkins)

        # Motivational banner
        render_dashboard_banner(avg_mood if daily_only is not None else None)

        st.divider()

        # ----------------------------
        # Build daily series
        # ----------------------------
        daily = df_view[df_view["entry_type"] == "daily"].copy()
        daily_mood = daily.groupby("day")["mood"].mean().reset_index()
        daily_mood["day_dt"] = pd.to_datetime(daily_mood["day"])

        total_counts = df_view.groupby("day").size().reset_index(name="entries_total")
        total_counts = make_date_range_df(start_day, end_inclusive).merge(
            total_counts, on="day", how="left"
        ).fillna(0)
        total_counts["entries_total"] = total_counts["entries_total"].astype(int)
        total_counts["day_dt"] = pd.to_datetime(total_counts["day"])

        span_days = (end_day - start_day).days + 1
        if span_days <= 10:
            x_fmt = "%a %b %d"
        elif span_days <= 60:
            x_fmt = "%b %d"
        else:
            x_fmt = "%b %Y"

        # ----------------------------
        # Mood trend
        # ----------------------------
        st.subheader("Mood trend")
        if daily_mood.empty:
            st.caption("No daily check-ins in this period yet.")
        else:
            mood_chart = (
                alt.Chart(daily_mood)
                .mark_line(point=True)
                .encode(
                    x=alt.X(
                        "day_dt:T",
                        title="Days",
                        axis=alt.Axis(format=x_fmt, labelOverlap=True),
                    ),
                    y=alt.Y("mood:Q", scale=alt.Scale(domain=[1, 10])),
                    tooltip=[alt.Tooltip("day_dt:T", title="Days"), "mood:Q"],
                )
                .properties(height=320)
            )
            st.altair_chart(mood_chart, use_container_width=True)

        st.divider()

        # ----------------------------
        # Engagement chart
        # ----------------------------
        st.subheader("Engagement trend")
        engagement_chart = (
            alt.Chart(total_counts)
            .mark_bar(color="#2CB67D")
            .encode(
                x=alt.X(
                    "day_dt:T",
                    title="Days",
                    axis=alt.Axis(format=x_fmt, labelOverlap=True),
                ),
                y=alt.Y("entries_total:Q", title="Total check-ins"),
                tooltip=[alt.Tooltip("day_dt:T", title="Days"), alt.Tooltip("entries_total:Q", title="Total check-ins")],
            )
            .properties(height=300)
        )
        st.altair_chart(engagement_chart, use_container_width=True)

        st.divider()

        # ----------------------------
        # Export
        # ----------------------------
        st.subheader("Export dashboard")

        metrics_text = (
            f"Period: {label} | "
            f"Average mood: {avg_mood} | Active days: {active_days} | "
            f"Total check-ins: {total_checkins} | Event check-ins: {event_checkins}"
        )
        png_bytes = dashboard_image_png_bytes(daily_mood, total_counts, metrics_text)
        pdf_bytes = build_dashboard_pdf(metrics_text, png_bytes)

        c1, c2 = st.columns(2)
        c1.download_button(
            "Download dashboard image (PNG)",
            png_bytes,
            file_name=f"revoraai_dashboard_range_{start_day}_{end_inclusive}.png",
            mime="image/png",
        )
        c2.download_button(
            "Download dashboard PDF",
            pdf_bytes,
            file_name=f"revoraai_dashboard_range_{start_day}_{end_inclusive}.pdf",
            mime="application/pdf",
        )

        st.divider()

        export_df = df_view.copy()
        export_df["timestamp"] = export_df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
        export_df["day"] = export_df["day"].astype(str)

        st.download_button(
            "Download data (CSV)",
            export_df.to_csv(index=False).encode("utf-8"),
            file_name=f"revoraai_data_range_{start_day}_{end_inclusive}.csv",
            mime="text/csv",
        )
elif page == "About":
    st.header("About RevoraAI")
    
    st.markdown(
        "**RevoraAI** is a prototype exploring how structured reflection can reduce session time spent reconstructing context and help clinicians see patterns faster."
    )

    st.markdown(
        """
It transforms daily check-ins and event-based reflections into visual trends, emotional pattern summaries, and AI-generated clinician-ready insights, all filtered by customizable date ranges.
"""
    )

    st.markdown("## What it does")

    st.markdown(
        """
- Daily check-in (once per day)  
- Event-based check-ins anytime something shifts  
- Mood rhythm & engagement analytics  
- Emotional pattern tracking
- AI generated user summary  
- AI-generated session summaries (editable before export)  
- Clinician-ready PDF export  
"""
    )

    st.markdown("## Tech Stack")

    st.markdown(
        """
**Frontend & App Framework:** Streamlit  
**Backend & Logic:** Python  
**Data Processing:** Pandas, NumPy  
**Visualization:** Altair, Matplotlib  
**AI Integration:** OpenAI API  
**Reporting & Export:** ReportLab (PDF generation)  
**State & Interaction:** Streamlit Session State  

**Built by:** Sanah Murtuza
"""
    )