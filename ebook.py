import streamlit as st
import streamlit.components.v1 as components
import os
import re
import json
import base64
import requests
import chardet
from opencc import OpenCC

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Hongguo Reader | 紅果私人書屋",
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- DIRECTORY & FILE SETUP ---
BOOKS_DIR = "books"
PROGRESS_FILE = "progress.json"
os.makedirs(BOOKS_DIR, exist_ok=True)

# --- GITHUB CLOUD SYNC HELPERS ---
def get_github_config():
    """Retrieve GitHub credentials from Streamlit Secrets if configured."""
    try:
        token = st.secrets.get("GITHUB_TOKEN", "")
        repo = st.secrets.get("GITHUB_REPO", "")
        branch = st.secrets.get("GITHUB_BRANCH", "main")
        if token and repo:
            return {"token": token, "repo": repo, "branch": branch}
    except Exception:
        pass
    return None

def github_push_file(file_path, content_bytes, commit_message):
    """Push a local file (book or progress.json) directly to GitHub repo."""
    cfg = get_github_config()
    if not cfg:
        return False, "GitHub secrets not configured (Local Mode)"
    
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{file_path}"
    headers = {
        "Authorization": f"token {cfg['token']}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Check if file exists to retrieve its SHA
    r = requests.get(url, headers=headers, params={"ref": cfg["branch"]})
    sha = r.json().get("sha") if r.status_code == 200 else None
    
    encoded_content = base64.b64encode(content_bytes).decode("utf-8")
    payload = {
        "message": commit_message,
        "content": encoded_content,
        "branch": cfg["branch"]
    }
    if sha:
        payload["sha"] = sha
        
    put_resp = requests.put(url, headers=headers, json=payload)
    if put_resp.status_code in (200, 201):
        return True, "Synced to GitHub Cloud"
    return False, f"Sync error: {put_resp.status_code}"

def github_pull_progress():
    """Fetch the latest progress.json from GitHub on initial session boot."""
    cfg = get_github_config()
    if not cfg:
        return None
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{PROGRESS_FILE}"
    headers = {
        "Authorization": f"token {cfg['token']}",
        "Accept": "application/vnd.github.v3+json"
    }
    r = requests.get(url, headers=headers, params={"ref": cfg["branch"]})
    if r.status_code == 200:
        try:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            data = json.loads(content)
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return data
        except Exception:
            pass
    return None

def github_sync_books_list():
    """Download any books stored in the GitHub repo's books/ folder that aren't local yet."""
    cfg = get_github_config()
    if not cfg:
        return
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{BOOKS_DIR}"
    headers = {
        "Authorization": f"token {cfg['token']}",
        "Accept": "application/vnd.github.v3+json"
    }
    r = requests.get(url, headers=headers, params={"ref": cfg["branch"]})
    if r.status_code == 200 and isinstance(r.json(), list):
        for item in r.json():
            fname = item.get("name", "")
            if fname.endswith(".txt"):
                local_path = os.path.join(BOOKS_DIR, fname)
                if not os.path.exists(local_path):
                    dl_url = item.get("download_url")
                    if dl_url:
                        book_resp = requests.get(dl_url)
                        if book_resp.status_code == 200:
                            with open(local_path, "wb") as f:
                                f.write(book_resp.content)

# --- PROGRESS MANAGEMENT ---
def load_progress():
    if "cloud_pulled" not in st.session_state:
        st.session_state.cloud_pulled = True
        github_sync_books_list()
        cloud_data = github_pull_progress()
        if cloud_data:
            return cloud_data
            
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_progress(progress_data, sync_cloud=True):
    raw_bytes = json.dumps(progress_data, ensure_ascii=False, indent=2).encode("utf-8")
    with open(PROGRESS_FILE, "wb") as f:
        f.write(raw_bytes)
    if sync_cloud:
        return github_push_file(PROGRESS_FILE, raw_bytes, "Auto-sync reading progress")
    return True, "Saved locally"

# --- TEXT DECODING & CHAPTER PARSING ---
def decode_txt_bytes(raw_bytes):
    """Automatically detect UTF-8, GB18030/GBK, Big5, or UTF-16 encodings."""
    for enc in ["utf-8", "utf-8-sig", "gb18030", "big5", "utf-16"]:
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    detected = chardet.detect(raw_bytes)
    encoding = detected.get("encoding") or "utf-8"
    return raw_bytes.decode(encoding, errors="replace")

@st.cache_data(show_spinner=False)
def parse_novel_chapters(book_name, convert_to_trad=False):
    path = os.path.join(BOOKS_DIR, book_name)
    if not os.path.exists(path):
        return [("Empty", "Book file not found.")]
        
    with open(path, "rb") as f:
        raw_bytes = f.read()
        
    text = decode_txt_bytes(raw_bytes)
    if convert_to_trad:
        cc = OpenCC("s2hk")
        text = cc.convert(text)
        
    # Regex matching common Chinese & English novel chapter headings
    pattern = re.compile(
        r"(?m)^[ 	　]*(?:第[0-9零一二三四五六七八九十百千万萬千兩]+[章節回卷集部篇]|Chapter\s+\d+|CHAPTER\s+\d+|【第.+?章】).*$"
    )
    matches = list(pattern.finditer(text))
    
    chapters = []
    if not matches:
        # Fallback: Slice into 4,500-character segments if no chapter headers exist
        chunk_size = 4500
        for idx, i in enumerate(range(0, len(text), chunk_size)):
            chapters.append((f"Part {idx + 1}", text[i:i + chunk_size]))
        return chapters
        
    # Include prologue if text exists before first chapter heading
    if matches[0].start() > 100:
        prologue = text[:matches[0].start()].strip()
        if prologue:
            chapters.append(("序章 / Prologue", prologue))
            
    for i, m in enumerate(matches):
        title = m.group(0).strip()
        start_pos = m.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start_pos:end_pos].strip()
        chapters.append((title, body))
        
    return chapters

# --- INITIALIZE SESSION STATE ---
progress_db = load_progress()
prefs = progress_db.get("_preferences", {})

# Ensure a sample welcome book exists on first launch
sample_path = os.path.join(BOOKS_DIR, "00_Welcome_Guide.txt")
if not os.path.exists(sample_path):
    sample_text = """第1章 歡迎使用紅果私人書屋 (Welcome Guide)
歡迎來到您的專屬雲端電子書閱讀器！

【核心操作指南】
1. 觸控與點擊暫停：在閱讀區域內，直接用手指輕觸螢幕（iPad 或觸控電腦）或點擊滑鼠，即可立即「開始 / 暫停」自動滾動。您也可以隨時按鍵盤空白鍵（Spacebar）控制。
2. 跨裝置雲端同步：當您切換章節或點擊左側邊欄的「儲存並同步進度」時，系統會自動將您的閱讀位置寫入 GitHub，無論在書房電腦、睡房電腦或 iPad 打開，都能無縫接續閱讀。
3. 語音朗讀 (Read Aloud)：點擊上方控制列的「🎧 朗讀」按鈕，系統會調用裝置內建的自然語音逐句朗讀，並自動高亮顯示當前句子及跟隨滾動。
4. 繁簡一鍵轉換：左側邊欄支援即時將簡體 TXT 小說轉換為繁體中文（香港標準字體）。

第2章 自動滾動與護眼模式測試
這是一段測試自動滾動與視覺舒適度的示範段落。

您可以透過上方控制列或左側設定欄，自由調整：
・滾動速度（Speed Slider：支援極慢速細讀至快速瀏覽）
・字體大小（Font Size：16px 至 40px）
・螢幕亮度調節（Brightness：內建柔和暗化遮罩，深夜閱讀不刺眼）
・四大主題背景：日間純白（Day）、夜間深邃黑（Dark）、經典羊皮紙（Sepia）、紅果護眼綠（Eye-Care Green）。

當自動滾動到達本章底部時，您可以直接點擊「下一章」按鈕，繼續享受沉浸式閱讀體驗！"""
    with open(sample_path, "w", encoding="utf-8") as f:
        f.write(sample_text)

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.markdown("### 📖 紅果私人書屋")
    
    # Cloud Status Badge
    if get_github_config():
        st.caption("🟢 GitHub 雲端同步已啟用")
    else:
        st.caption("🟡 本地模式 (設定 Secrets 即可啟用跨裝置同步)")

    # 1. Upload New Book
    with st.expander("➕ 上傳新書 (.txt)", expanded=False):
        uploaded_file = st.file_uploader("選擇或拖曳 TXT 檔案", type=["txt"])
        if uploaded_file is not None:
            save_path = os.path.join(BOOKS_DIR, uploaded_file.name)
            file_bytes = uploaded_file.getvalue()
            with open(save_path, "wb") as f:
                f.write(file_bytes)
            ok, msg = github_push_file(f"{BOOKS_DIR}/{uploaded_file.name}", file_bytes, f"Add book {uploaded_file.name}")
            st.success(f"已載入《{uploaded_file.name}》 ({msg})")
            progress_db["_last_book"] = uploaded_file.name
            save_progress(progress_db, sync_cloud=True)
            st.rerun()

    # 2. Select Book from Library
    available_books = sorted([f for f in os.listdir(BOOKS_DIR) if f.endswith(".txt")])
    last_book = progress_db.get("_last_book", available_books[0] if available_books else None)
    default_book_idx = available_books.index(last_book) if last_book in available_books else 0

    selected_book = st.selectbox("📚 選擇書籍", available_books, index=default_book_idx)

    # 3. Language Conversion Toggle (Simplified <-> Traditional)
    trad_toggle = st.toggle("繁體中文顯示 (簡轉繁)", value=prefs.get("trad_chinese", True))

    # Parse Chapters
    chapters = parse_novel_chapters(selected_book, convert_to_trad=trad_toggle)
    chapter_titles = [c[0] for c in chapters]

    # Load saved chapter & scroll ratio for this specific book
    book_state = progress_db.get(selected_book, {"chapter_idx": 0, "scroll_pct": 0.0})
    saved_chap_idx = min(book_state.get("chapter_idx", 0), len(chapters) - 1)

    # Handle Next/Prev buttons via session state
    if "current_book" not in st.session_state or st.session_state.current_book != selected_book:
        st.session_state.current_book = selected_book
        st.session_state.chapter_idx = saved_chap_idx
        st.session_state.scroll_pct = book_state.get("scroll_pct", 0.0)

    # Chapter selector dropdown
    selected_chap_idx = st.selectbox(
        "📑 章節跳轉",
        range(len(chapter_titles)),
        format_func=lambda i: chapter_titles[i],
        index=st.session_state.chapter_idx
    )
    if selected_chap_idx != st.session_state.chapter_idx:
        st.session_state.chapter_idx = selected_chap_idx
        st.session_state.scroll_pct = 0.0
        progress_db[selected_book] = {"chapter_idx": selected_chap_idx, "scroll_pct": 0.0}
        progress_db["_last_book"] = selected_book
        save_progress(progress_db, sync_cloud=True)
        st.rerun()

    # Chapter Navigation Buttons
    col_prev, col_next = st.columns(2)
    with col_prev:
        if st.button("⬅️ 上一章", use_container_width=True, disabled=(st.session_state.chapter_idx <= 0)):
            st.session_state.chapter_idx -= 1
            st.session_state.scroll_pct = 0.0
            progress_db[selected_book] = {"chapter_idx": st.session_state.chapter_idx, "scroll_pct": 0.0}
            progress_db["_last_book"] = selected_book
            save_progress(progress_db, sync_cloud=True)
            st.rerun()
    with col_next:
        if st.button("下一章 ➡️", use_container_width=True, disabled=(st.session_state.chapter_idx >= len(chapters) - 1)):
            st.session_state.chapter_idx += 1
            st.session_state.scroll_pct = 0.0
            progress_db[selected_book] = {"chapter_idx": st.session_state.chapter_idx, "scroll_pct": 0.0}
            progress_db["_last_book"] = selected_book
            save_progress(progress_db, sync_cloud=True)
            st.rerun()

    st.divider()
    st.markdown("#### ⚙️ 預設閱讀偏好")
    theme_choice = st.selectbox(
        "🎨 背景主題",
        ["夜間深黑 (Dark)", "日間純白 (Day)", "紅果護眼綠 (Green)", "溫潤羊皮紙 (Sepia)"],
        index=prefs.get("theme_idx", 0)
    )
    default_speed = st.slider("🚀 預設滾動速度", 1, 100, prefs.get("speed", 28))
    default_font = st.slider("🔠 預設字體大小 (px)", 16, 42, prefs.get("font_size", 24))
    default_bright = st.slider("🔆 預設螢幕亮度 (%)", 20, 100, prefs.get("brightness", 90))
    default_tts_rate = st.slider("🗣️ 語音朗讀語速", 0.5, 2.0, prefs.get("tts_rate", 1.1), 0.1)

    # Bookmark & Cloud Sync Section
    st.divider()
    st.markdown("#### ☁️ 跨裝置書籤同步")
    manual_pct = st.number_input(
        "本章閱讀進度 (%)",
        min_value=0.0, max_value=100.0,
        value=float(round(st.session_state.scroll_pct, 1)),
        step=5.0,
        help="在閱讀器內會即時顯示目前進度%，可在此鎖定或直接儲存章節進度"
    )
    if st.button("💾 儲存設定與雲端書籤", type="primary", use_container_width=True):
        progress_db["_last_book"] = selected_book
        progress_db[selected_book] = {
            "chapter_idx": st.session_state.chapter_idx,
            "scroll_pct": manual_pct
        }
        theme_options = ["夜間深黑 (Dark)", "日間純白 (Day)", "紅果護眼綠 (Green)", "溫潤羊皮紙 (Sepia)"]
        progress_db["_preferences"] = {
            "trad_chinese": trad_toggle,
            "theme_idx": theme_options.index(theme_choice),
            "speed": default_speed,
            "font_size": default_font,
            "brightness": default_bright,
            "tts_rate": default_tts_rate
        }
        ok, msg = save_progress(progress_db, sync_cloud=True)
        st.toast(f"✅ 已同步至雲端！({msg})")

# --- PREPARE CHAPTER HTML ---
current_title, current_body = chapters[st.session_state.chapter_idx]
paragraphs = [p.strip() for p in current_body.split("
") if p.strip()]
paragraphs_json = json.dumps(paragraphs, ensure_ascii=False)

theme_map = {
    "夜間深黑 (Dark)": "dark",
    "日間純白 (Day)": "day",
    "紅果護眼綠 (Green)": "green",
    "溫潤羊皮紙 (Sepia)": "sepia"
}
initial_theme = theme_map.get(theme_choice, "dark")

# Trim Streamlit default top padding so the reader feels full-screen on iPad & PC
st.markdown("""
<style>
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0rem !important;
        max-width: 100% !important;
    }
    header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# --- INTERACTIVE HONGGUO READER CANVAS (HTML/JS/CSS) ---
reader_html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
    :root {{
        --bg-color: #121212;
        --text-color: #d4d4d4;
        --accent-color: #ff5e3a;
        --bar-bg: rgba(28, 28, 30, 0.94);
        --highlight-bg: rgba(255, 94, 58, 0.25);
    }}
    body.theme-day {{
        --bg-color: #f8f9fa;
        --text-color: #212529;
        --accent-color: #d9480f;
        --bar-bg: rgba(255, 255, 255, 0.94);
        --highlight-bg: rgba(255, 193, 7, 0.35);
    }}
    body.theme-green {{
        --bg-color: #cce8cf;
        --text-color: #1b3320;
        --accent-color: #2b8a3e;
        --bar-bg: rgba(220, 242, 222, 0.95);
        --highlight-bg: rgba(43, 138, 62, 0.22);
    }}
    body.theme-sepia {{
        --bg-color: #f4ecd8;
        --text-color: #3c2f2f;
        --accent-color: #b05b3b;
        --bar-bg: rgba(248, 241, 224, 0.95);
        --highlight-bg: rgba(176, 91, 59, 0.22);
    }}
    body.theme-dark {{
        --bg-color: #121212;
        --text-color: #cfcfcf;
        --accent-color: #ff6b4a;
        --bar-bg: rgba(26, 26, 28, 0.95);
        --highlight-bg: rgba(255, 107, 74, 0.25);
    }}

    * {{
        box-sizing: border-box;
        -webkit-tap-highlight-color: transparent;
    }}

    body {{
        margin: 0;
        padding: 0;
        background-color: var(--bg-color);
        color: var(--text-color);
        font-family: -apple-system, BlinkMacSystemFont, "PingFang HK", "Microsoft JhengHei", "Noto Sans TC", sans-serif;
        transition: background-color 0.25s ease, color 0.25s ease;
        overflow: hidden;
    }}

    /* Brightness Dimmer Overlay */
    #brightness-overlay {{
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background: #000;
        opacity: {(100 - default_bright) / 100.0};
        pointer-events: none;
        z-index: 900;
        transition: opacity 0.1s ease;
    }}

    /* Top Floating Control Bar */
    #top-bar {{
        position: fixed;
        top: 0; left: 0; right: 0;
        background: var(--bar-bg);
        backdrop-filter: blur(10px);
        padding: 10px 18px;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        border-bottom: 1px solid rgba(128,128,128,0.2);
        z-index: 800;
        font-size: 13px;
    }}

    .ctrl-group {{
        display: flex;
        align-items: center;
        gap: 8px;
    }}

    .btn {{
        background: var(--accent-color);
        color: #fff;
        border: none;
        border-radius: 20px;
        padding: 6px 14px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.18);
    }}
    .btn.secondary {{
        background: rgba(128,128,128,0.22);
        color: var(--text-color);
        box-shadow: none;
    }}
    .btn:active {{
        transform: scale(0.96);
    }}

    input[type=range] {{
        width: 85px;
        accent-color: var(--accent-color);
        cursor: pointer;
    }}

    /* Scrollable Reading Viewport */
    #reader-viewport {{
        height: 88vh;
        overflow-y: auto;
        padding: 75px 12% 180px 12%;
        scroll-behavior: auto;
        cursor: pointer;
        user-select: none;
    }}

    @media (max-width: 768px) {{
        #reader-viewport {{
            padding: 85px 6% 180px 6%;
        }}
    }}

    #chapter-title {{
        font-size: 1.45em;
        font-weight: 700;
        margin-bottom: 1.2em;
        color: var(--accent-color);
        border-bottom: 1px solid rgba(128,128,128,0.2);
        padding-bottom: 0.5em;
    }}

    p.novel-para {{
        font-size: {default_font}px;
        line-height: 1.85;
        margin: 0 0 1.15em 0;
        text-indent: 2em;
        letter-spacing: 0.03em;
        border-radius: 6px;
        padding: 2px 4px;
        transition: background-color 0.2s;
    }}

    p.novel-para.speaking-active {{
        background-color: var(--highlight-bg);
    }}

    /* Center Toast Indicator when Tapping Screen */
    #tap-toast {{
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: rgba(0, 0, 0, 0.78);
        color: #fff;
        padding: 14px 26px;
        border-radius: 30px;
        font-size: 16px;
        font-weight: 600;
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.25s ease;
        z-index: 950;
    }}

    /* Bottom Status Ribbon */
    #bottom-ribbon {{
        position: fixed;
        bottom: 0; left: 0; right: 0;
        background: var(--bar-bg);
        padding: 6px 18px;
        font-size: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-top: 1px solid rgba(128,128,128,0.18);
        z-index: 800;
        opacity: 0.9;
    }}
</style>
</head>
<body class="theme-{initial_theme}">

<div id="brightness-overlay"></div>
<div id="tap-toast">⏸ 已暫停滾動</div>

<!-- Top Floating Hongguo Toolbar -->
<div id="top-bar">
    <div class="ctrl-group">
        <button id="scroll-toggle-btn" class="btn" onclick="toggleScroll(event)">▶️ 自動滾動</button>
        <span title="滾動速度">🚀</span>
        <input type="range" id="speed-slider" min="1" max="100" value="{default_speed}" oninput="updateSpeed(this.value)">
        <span id="speed-val">{default_speed}</span>
    </div>

    <div class="ctrl-group">
        <span title="字體大小">🔠</span>
        <input type="range" id="font-slider" min="16" max="42" value="{default_font}" oninput="updateFont(this.value)">
        <span title="螢幕亮度">🔆</span>
        <input type="range" id="bright-slider" min="20" max="100" value="{default_bright}" oninput="updateBrightness(this.value)">
    </div>

    <div class="ctrl-group">
        <button class="btn secondary" onclick="cycleTheme(event)">🎨 主題</button>
        <button id="tts-btn" class="btn secondary" onclick="toggleTTS(event)">🎧 朗讀</button>
    </div>
</div>

<!-- Main Touch-to-Pause Reading Area -->
<div id="reader-viewport">
    <div id="chapter-title">{current_title}</div>
    <div id="paragraphs-container"></div>
    <div style="text-align:center; padding: 40px 0; opacity: 0.6; font-size: 14px;">
        — 本章完結 (請從左側欄點擊「下一章 ➡️」) —
    </div>
</div>

<!-- Bottom Progress Ribbon -->
<div id="bottom-ribbon">
    <span>💡 輕觸螢幕任意位置或按 Space 鍵即可「暫停 / 繼續」滾動</span>
    <span id="progress-indicator">進度: 0.0%</span>
</div>

<script>
    const paragraphs = {paragraphs_json};
    const bookKey = "hongguo_pos_" + {json.dumps(selected_book)} + "_ch_" + {st.session_state.chapter_idx};
    const initialCloudPct = { float(st.session_state.scroll_pct) };

    const container = document.getElementById("paragraphs-container");
    const viewport = document.getElementById("reader-viewport");
    const scrollBtn = document.getElementById("scroll-toggle-btn");
    const ttsBtn = document.getElementById("tts-btn");
    const toast = document.getElementById("tap-toast");
    const progLabel = document.getElementById("progress-indicator");

    // Populate Paragraphs
    paragraphs.forEach((txt, idx) => {{
        const p = document.createElement("p");
        p.className = "novel-para";
        p.id = "para-" + idx;
        p.textContent = txt;
        container.appendChild(p);
    }});

    // Restore Scroll Position (LocalStorage or Cloud Synced Percentage)
    window.addEventListener("load", () => {{
        const savedLocal = localStorage.getItem(bookKey);
        const maxScroll = viewport.scrollHeight - viewport.clientHeight;
        if (initialCloudPct > 0 && maxScroll > 0) {{
            viewport.scrollTop = (initialCloudPct / 100.0) * maxScroll;
        }} else if (savedLocal !== null && maxScroll > 0) {{
            viewport.scrollTop = (parseFloat(savedLocal) / 100.0) * maxScroll;
        }}
    }});

    // Autoscroll Engine using requestAnimationFrame with Sub-Pixel Accumulator
    let isScrolling = false;
    let scrollSpeed = {default_speed};
    let pixelAccumulator = 0;
    let lastFrameTime = null;

    function autoScrollStep(timestamp) {{
        if (!isScrolling) return;
        if (!lastFrameTime) lastFrameTime = timestamp;
        const deltaMs = timestamp - lastFrameTime;
        lastFrameTime = timestamp;

        // Map slider 1..100 to smooth pixels per second (8 px/sec to 220 px/sec)
        const pxPerSec = 6 + Math.pow(scrollSpeed, 1.35) * 0.42;
        pixelAccumulator += (pxPerSec * deltaMs) / 1000.0;

        if (pixelAccumulator >= 1.0) {{
            const step = Math.floor(pixelAccumulator);
            viewport.scrollTop += step;
            pixelAccumulator -= step;
        }}

        // Auto-stop at bottom of chapter
        if (viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 2) {{
            setScrollingState(false, "🎉 已到達本章底部");
            return;
        }}
        requestAnimationFrame(autoScrollStep);
    }}

    function showToast(msg) {{
        toast.textContent = msg;
        toast.style.opacity = "1";
        clearTimeout(window.toastTimer);
        window.toastTimer = setTimeout(() => {{
            toast.style.opacity = "0";
        }}, 950);
    }}

    function setScrollingState(active, customMsg = null) {{
        isScrolling = active;
        if (isScrolling) {{
            lastFrameTime = null;
            scrollBtn.innerHTML = "⏸ 暫停滾動";
            showToast(customMsg || "▶️ 開始自動滾動");
            requestAnimationFrame(autoScrollStep);
        }} else {{
            scrollBtn.innerHTML = "▶️ 自動滾動";
            showToast(customMsg || "⏸ 已暫停滾動");
        }}
    }}

    function toggleScroll(e) {{
        if (e) e.stopPropagation();
        setScrollingState(!isScrolling);
    }}

    // Touch / Click on Reading Viewport to Pause or Resume Instantly
    let touchStartY = 0;
    let touchStartX = 0;
    let isDraggingTouch = false;

    viewport.addEventListener("touchstart", (e) => {{
        touchStartY = e.touches[0].clientY;
        touchStartX = e.touches[0].clientX;
        isDraggingTouch = false;
    }}, {{passive: true}});

    viewport.addEventListener("touchmove", (e) => {{
        const dy = Math.abs(e.touches[0].clientY - touchStartY);
        const dx = Math.abs(e.touches[0].clientX - touchStartX);
        if (dy > 10 || dx > 10) {{
            isDraggingTouch = true;
            // If user manually flicks the screen while autoscrolling, pause gently
            if (isScrolling) {{
                setScrollingState(false, "⏸ 手動滑動已暫停自動滾動");
            }}
        }}
    }}, {{passive: true}});

    viewport.addEventListener("click", (e) => {{
        // Ignore if user was selecting text or dragging
        if (isDraggingTouch) return;
        setScrollingState(!isScrolling);
    }});

    // Spacebar Support for Desktop
    window.addEventListener("keydown", (e) => {{
        if (e.code === "Space") {{
            e.preventDefault();
            setScrollingState(!isScrolling);
        }}
    }});

    // Track Scroll Percentage & Save to LocalStorage
    viewport.addEventListener("scroll", () => {{
        const maxScroll = viewport.scrollHeight - viewport.clientHeight;
        if (maxScroll > 0) {{
            const pct = Math.min(100, Math.max(0, (viewport.scrollTop / maxScroll) * 100)).toFixed(1);
            progLabel.textContent = "本章進度: " + pct + "%";
            localStorage.setItem(bookKey, pct);
        }}
    }});

    // UI Sliders
    function updateSpeed(val) {{
        scrollSpeed = parseInt(val, 10);
        document.getElementById("speed-val").textContent = val;
    }}

    function updateFont(val) {{
        document.querySelectorAll("p.novel-para").forEach(p => {{
            p.style.fontSize = val + "px";
        }});
    }}

    function updateBrightness(val) {{
        const overlay = document.getElementById("brightness-overlay");
        overlay.style.opacity = ((100 - parseInt(val, 10)) / 100.0).toFixed(2);
    }}

    const themes = ["theme-dark", "theme-day", "theme-green", "theme-sepia"];
    let currentThemeIdx = themes.indexOf("theme-{initial_theme}");
    function cycleTheme(e) {{
        if (e) e.stopPropagation();
        document.body.classList.remove(themes[currentThemeIdx]);
        currentThemeIdx = (currentThemeIdx + 1) % themes.length;
        document.body.classList.add(themes[currentThemeIdx]);
    }}

    // --- READ ALOUD (WEB SPEECH API TTS) ---
    let isSpeaking = false;
    let currentParaIdx = 0;
    const synth = window.speechSynthesis;

    function findFirstVisibleParagraph() {{
        const viewTop = viewport.scrollTop + 80;
        for (let i = 0; i < paragraphs.length; i++) {{
            const el = document.getElementById("para-" + i);
            if (el && (el.offsetTop + el.offsetHeight) > viewTop) {{
                return i;
            }}
        }}
        return 0;
    }}

    function speakParagraph(index) {{
        if (!isSpeaking || index >= paragraphs.length) {{
            stopTTS();
            return;
        }}
        currentParaIdx = index;
        document.querySelectorAll("p.novel-para").forEach(p => p.classList.remove("speaking-active"));
        const activeEl = document.getElementById("para-" + index);
        if (activeEl) {{
            activeEl.classList.add("speaking-active");
            activeEl.scrollIntoView({{ behavior: "smooth", block: "center" }});
        }}

        const utter = new SpeechSynthesisUtterance(paragraphs[index]);
        // Automatically select Chinese or English voice based on paragraph characters
        const hasChinese = /[\u4e00-\u9fa5]/.test(paragraphs[index]);
        utter.lang = hasChinese ? "zh-HK" : "en-US";
        utter.rate = {default_tts_rate};

        utter.onend = () => {{
            if (isSpeaking) {{
                speakParagraph(index + 1);
            }}
        }};
        utter.onerror = () => {{
            stopTTS();
        }};
        synth.speak(utter);
    }}

    function stopTTS() {{
        isSpeaking = false;
        if (synth) synth.cancel();
        ttsBtn.innerHTML = "🎧 朗讀";
        document.querySelectorAll("p.novel-para").forEach(p => p.classList.remove("speaking-active"));
    }}

    function toggleTTS(e) {{
        if (e) e.stopPropagation();
        if (!("speechSynthesis" in window)) {{
            alert("您的瀏覽器不支援內建語音朗讀功能");
            return;
        }}
        if (isSpeaking) {{
            stopTTS();
            showToast("🔇 已停止語音朗讀");
        }} else {{
            if (isScrolling) setScrollingState(false);
            isSpeaking = true;
            ttsBtn.innerHTML = "⏹ 停止朗讀";
            const startIdx = findFirstVisibleParagraph();
            showToast("🎧 開始語音朗讀");
            synth.cancel();
            speakParagraph(startIdx);
        }}
    }}
</script>
</body>
</html>
"""

components.html(reader_html, height=820, scrolling=False)
