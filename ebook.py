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
    page_title="My Books | 紅果私人書屋",
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
        return False, "Local Mode (Set GitHub Secrets for Cloud Sync)"
    
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{file_path}"
    headers = {
        "Authorization": f"token {cfg['token']}",
        "Accept": "application/vnd.github.v3+json"
    }
    
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
        return [{"title": "Empty", "paras": ["Book file not found."]}]
        
    with open(path, "rb") as f:
        raw_bytes = f.read()
        
    text = decode_txt_bytes(raw_bytes)
    if convert_to_trad:
        cc = OpenCC("s2hk")
        text = cc.convert(text)
        
    pattern = re.compile(
        r"(?m)^[ \t　]*(?:第[0-9零一二三四五六七八九十百千万萬千兩]+[章節回卷集部篇]|Chapter\s+\d+|CHAPTER\s+\d+|【第.+?章】).*$"
    )
    matches = list(pattern.finditer(text))
    
    chapters = []
    if not matches:
        chunk_size = 4500
        for idx, i in enumerate(range(0, len(text), chunk_size)):
            segment = text[i:i + chunk_size]
            paras = [p.strip() for p in segment.splitlines() if p.strip()]
            chapters.append({"title": f"Part {idx + 1}", "paras": paras})
        return chapters
        
    if matches[0].start() > 100:
        prologue = text[:matches[0].start()].strip()
        if prologue:
            paras = [p.strip() for p in prologue.splitlines() if p.strip()]
            if paras:
                chapters.append({"title": "序章 / Prologue", "paras": paras})
            
    for i, m in enumerate(matches):
        title = m.group(0).strip()
        start_pos = m.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start_pos:end_pos].strip()
        paras = [p.strip() for p in body.splitlines() if p.strip()]
        if not paras:
            paras = ["（本章無正文內容）"]
        chapters.append({"title": title, "paras": paras})
        
    return chapters

# --- INITIALIZE SESSION STATE ---
progress_db = load_progress()
prefs = progress_db.get("_preferences", {})

# Ensure a sample welcome book exists on first launch
sample_path = os.path.join(BOOKS_DIR, "00_Welcome_Guide.txt")
if not os.path.exists(sample_path):
    sample_lines = [
        "第1章 歡迎使用紅果私人書屋 v3.0 (全觸控無縫滾動版)",
        "歡迎來到您的專屬雲端電子書閱讀器！",
        "【v3.0 核心功能指南】",
        "1. 頂部懸浮控制列：現在頂部控制列完全獨立懸浮在閱讀區上方，您可以直接拖曳滑桿調整「滾動速度、字體大小、亮度」，或點擊「📑 目錄」、「🎨 主題」、「🎧 朗讀」。",
        "2. 無縫跨章自動滾動：開啟「▶️ 自動滾動」後，系統會在接近章節底部時自動把下一章接在下方（第1章 → 第2章 → 第3章……），永遠不會中途停頓，直到您輕觸螢幕暫停為止！",
        "3. 速度與背景永久記憶：您調整的滾動速度、字體大小、亮度與背景主題，會即時儲存在瀏覽器中，換章節或重開網頁都不會跳回預設值。",
        "第2章 跨章無縫接續測試段落",
        "您現在看到的是第2章的內容！當從第1章往下滾動時，系統已經自動把第2章接在下方，滾動速度、背景顏色與字體大小完全保持不變。",
        "無論在 iPad、睡房觸控電腦或書房電腦上，只要用手指輕觸文字區域任意位置（或按鍵盤 Space 鍵），即可隨時暫停或繼續滾動。"
    ]
    with open(sample_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(sample_lines))

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.markdown("### 📖 My Books | 私人書屋")
    
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
    if trad_toggle != prefs.get("trad_chinese", True):
        prefs["trad_chinese"] = trad_toggle
        progress_db["_preferences"] = prefs
        save_progress(progress_db, sync_cloud=False)

    # Parse Chapters
    chapters = parse_novel_chapters(selected_book, convert_to_trad=trad_toggle)
    chapter_titles = [c["title"] for c in chapters]

    # Load saved chapter & scroll ratio for this specific book
    book_state = progress_db.get(selected_book, {"chapter_idx": 0, "scroll_pct": 0.0})
    saved_chap_idx = min(book_state.get("chapter_idx", 0), max(0, len(chapters) - 1))

    if "current_book" not in st.session_state or st.session_state.current_book != selected_book:
        st.session_state.current_book = selected_book
        st.session_state.chapter_idx = saved_chap_idx
        st.session_state.scroll_pct = book_state.get("scroll_pct", 0.0)
        progress_db["_last_book"] = selected_book
        save_progress(progress_db, sync_cloud=False)

    st.divider()
    st.markdown("#### ☁️ 跨裝置雲端書籤")
    st.caption("在同一部裝置上，您的章節、滾動位置、速度與背景主題會**每秒自動記憶**。若要換去另一部電腦或 iPad 接續看，請在下方同步章節：")
    
    sync_chap_idx = st.selectbox(
        "📑 選擇要同步到雲端的章節",
        range(len(chapter_titles)),
        format_func=lambda i: f"({i+1}/{len(chapter_titles)}) {chapter_titles[i]}",
        index=st.session_state.chapter_idx
    )
    sync_pct = st.number_input(
        "本章進度 (%)",
        min_value=0.0, max_value=100.0,
        value=float(round(st.session_state.scroll_pct, 1)),
        step=5.0
    )
    if st.button("💾 儲存並同步雲端書籤", type="primary", use_container_width=True):
        st.session_state.chapter_idx = sync_chap_idx
        st.session_state.scroll_pct = sync_pct
        progress_db["_last_book"] = selected_book
        progress_db[selected_book] = {
            "chapter_idx": sync_chap_idx,
            "scroll_pct": sync_pct
        }
        ok, msg = save_progress(progress_db, sync_cloud=True)
        st.toast(f"✅ 已同步至 GitHub 雲端！({msg})")
        st.rerun()

# --- CRITICAL CSS FIX: PREVENT STREAMLIT HEADER FROM BLOCKING TOP BAR CLICKS ---
st.markdown("""
<style>
    /* Place the reader container just below the top-left sidebar toggle button & lock outer scroll */
    .block-container {
        padding-top: 2.6rem !important;
        padding-bottom: 0rem !important;
        padding-left: 0.4rem !important;
        padding-right: 0.4rem !important;
        max-width: 100% !important;
    }
    /* Restrict Streamlit's header to ONLY the top-left 60px corner so it NEVER covers the reader's top bar */
    header[data-testid="stHeader"] {
        background: transparent !important;
        width: 60px !important;
        max-width: 60px !important;
        right: auto !important;
        height: 2.5rem !important;
        pointer-events: none !important;
    }
    .stAppToolbar, [data-testid="stToolbar"] {
        width: 60px !important;
        max-width: 60px !important;
        right: auto !important;
        background: transparent !important;
        pointer-events: none !important;
    }
    /* Hide right-side Streamlit toolbar clutter */
    .stAppDeployButton, [data-testid="stToolbarActions"], [data-testid="stStatusWidget"], [data-testid="stDecoration"] {
        display: none !important;
    }
    /* Make ONLY the sidebar expand/collapse button clickable and clearly visible */
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapsedControl"],
    header[data-testid="stHeader"] button {
        pointer-events: auto !important;
        background-color: #ff6b4a !important;
        color: #ffffff !important;
        border-radius: 0 20px 20px 0 !important;
        padding: 4px 10px !important;
        top: 4px !important;
        left: 0px !important;
        z-index: 999999 !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3) !important;
    }
    [data-testid="collapsedControl"] svg,
    [data-testid="stSidebarCollapsedControl"] svg,
    header[data-testid="stHeader"] button svg {
        fill: #ffffff !important;
        stroke: #ffffff !important;
    }
    /* Size the reader iframe dynamically to fit any iPad or PC screen without outer page scrolling */
    iframe {
        height: calc(100dvh - 2.9rem) !important;
        min-height: 480px !important;
        border: none !important;
    }
</style>
""", unsafe_allow_html=True)

# Base64-encode chapters JSON so special characters/quotes in any TXT file can never break JS
chapters_b64 = base64.b64encode(json.dumps(chapters, ensure_ascii=False).encode("utf-8")).decode("ascii")
book_name_b64 = base64.b64encode(selected_book.encode("utf-8")).decode("ascii")

default_speed = int(prefs.get("speed", 28))
default_font = int(prefs.get("font_size", 24))
default_bright = int(prefs.get("brightness", 90))
default_tts_rate = float(prefs.get("tts_rate", 1.1))

# --- INTERACTIVE HONGGUO READER CANVAS ---
reader_html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
    :root {{
        --bg-color: #121212;
        --text-color: #cfcfcf;
        --accent-color: #ff6b4a;
        --bar-bg: rgba(26, 26, 28, 0.98);
        --highlight-bg: rgba(255, 107, 74, 0.25);
        --drawer-bg: #1e1e20;
    }}
    body.theme-day {{
        --bg-color: #f8f9fa;
        --text-color: #212529;
        --accent-color: #d9480f;
        --bar-bg: rgba(255, 255, 255, 0.98);
        --highlight-bg: rgba(255, 193, 7, 0.35);
        --drawer-bg: #ffffff;
    }}
    body.theme-green {{
        --bg-color: #cce8cf;
        --text-color: #1b3320;
        --accent-color: #2b8a3e;
        --bar-bg: rgba(220, 242, 222, 0.98);
        --highlight-bg: rgba(43, 138, 62, 0.22);
        --drawer-bg: #daf0dc;
    }}
    body.theme-sepia {{
        --bg-color: #f4ecd8;
        --text-color: #3c2f2f;
        --accent-color: #b05b3b;
        --bar-bg: rgba(248, 241, 224, 0.98);
        --highlight-bg: rgba(176, 91, 59, 0.22);
        --drawer-bg: #efe4c8;
    }}
    body.theme-dark {{
        --bg-color: #121212;
        --text-color: #cfcfcf;
        --accent-color: #ff6b4a;
        --bar-bg: rgba(26, 26, 28, 0.98);
        --highlight-bg: rgba(255, 107, 74, 0.25);
        --drawer-bg: #1e1e20;
    }}

    * {{
        box-sizing: border-box;
        -webkit-tap-highlight-color: transparent;
    }}

    html, body {{
        height: 100%;
        width: 100%;
        margin: 0;
        padding: 0;
        background-color: var(--bg-color);
        color: var(--text-color);
        font-family: -apple-system, BlinkMacSystemFont, "PingFang HK", "Microsoft JhengHei", "Noto Sans TC", sans-serif;
        transition: background-color 0.2s ease, color 0.2s ease;
        overflow: hidden;
        position: relative;
    }}

    /* Brightness Dimmer Overlay */
    #brightness-overlay {{
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background: #000;
        opacity: 0.1;
        pointer-events: none;
        z-index: 900;
        transition: opacity 0.1s ease;
    }}

    /* Pinned Floating Top Control Bar */
    #top-bar {{
        position: fixed;
        top: 0; left: 0; right: 0;
        min-height: 54px;
        background: var(--bar-bg);
        padding: 8px 16px;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        border-bottom: 1px solid rgba(128,128,128,0.25);
        z-index: 1000;
        font-size: 13px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.15);
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
        padding: 7px 14px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.18);
        white-space: nowrap;
        touch-action: manipulation;
    }}
    .btn.secondary {{
        background: rgba(128,128,128,0.24);
        color: var(--text-color);
        box-shadow: none;
    }}
    .btn:active {{
        transform: scale(0.95);
    }}

    input[type=range] {{
        width: 90px;
        height: 24px;
        accent-color: var(--accent-color);
        cursor: pointer;
        touch-action: pan-y;
    }}

    /* Built-in Floating Chapter Drawer (目錄) */
    #toc-drawer {{
        position: fixed;
        top: 58px;
        right: 16px;
        width: 340px;
        max-width: 92vw;
        max-height: 72vh;
        background: var(--drawer-bg);
        color: var(--text-color);
        border: 1px solid rgba(128,128,128,0.35);
        border-radius: 12px;
        box-shadow: 0 12px 32px rgba(0,0,0,0.5);
        overflow-y: auto;
        -webkit-overflow-scrolling: touch;
        z-index: 1100;
        display: none;
        padding: 10px;
    }}
    .toc-header {{
        font-weight: 700;
        font-size: 14px;
        padding: 6px 10px 10px 10px;
        border-bottom: 1px solid rgba(128,128,128,0.2);
        margin-bottom: 6px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .toc-item {{
        padding: 10px 12px;
        border-radius: 8px;
        cursor: pointer;
        font-size: 14px;
        border-bottom: 1px solid rgba(128,128,128,0.1);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .toc-item:hover, .toc-item.active-chap {{
        background: var(--highlight-bg);
        color: var(--accent-color);
        font-weight: 700;
    }}

    /* Scrollable Reading Viewport Anchored Between Top Bar and Bottom Ribbon */
    #reader-viewport {{
        position: absolute;
        top: 54px;
        bottom: 32px;
        left: 0;
        right: 0;
        overflow-y: auto;
        -webkit-overflow-scrolling: touch;
        padding: 28px 12% 160px 12%;
        scroll-behavior: auto;
        cursor: pointer;
        user-select: none;
    }}

    @media (max-width: 768px) {{
        #top-bar {{
            padding: 6px 10px;
            min-height: 82px;
        }}
        #reader-viewport {{
            top: 82px;
            padding: 20px 6% 160px 6%;
        }}
        #toc-drawer {{
            top: 86px;
        }}
    }}

    .chapter-block {{
        margin-bottom: 50px;
        padding-bottom: 20px;
        border-bottom: 1px dashed rgba(128,128,128,0.25);
    }}

    .chapter-heading {{
        font-size: 1.45em;
        font-weight: 700;
        margin: 0.6em 0 1.1em 0;
        color: var(--accent-color);
        border-left: 4px solid var(--accent-color);
        padding-left: 12px;
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

    /* Center Toast Indicator */
    #tap-toast {{
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: rgba(0, 0, 0, 0.82);
        color: #fff;
        padding: 14px 26px;
        border-radius: 30px;
        font-size: 16px;
        font-weight: 600;
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.25s ease;
        z-index: 1200;
    }}

    /* Pinned Bottom Status Ribbon */
    #bottom-ribbon {{
        position: fixed;
        bottom: 0; left: 0; right: 0;
        height: 32px;
        background: var(--bar-bg);
        padding: 0 18px;
        font-size: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-top: 1px solid rgba(128,128,128,0.2);
        z-index: 1000;
    }}
</style>
</head>
<body class="theme-dark">

<div id="brightness-overlay"></div>
<div id="tap-toast">⏸ 已暫停滾動</div>

<!-- Top Floating Hongguo Toolbar -->
<div id="top-bar">
    <div class="ctrl-group">
        <button id="scroll-toggle-btn" type="button" class="btn">▶️ 自動滾動</button>
        <span title="滾動速度">🚀</span>
        <input type="range" id="speed-slider" min="1" max="100" value="{default_speed}">
        <span id="speed-val" style="min-width:24px; font-weight:700;">{default_speed}</span>
    </div>

    <div class="ctrl-group">
        <span title="字體大小">🔠</span>
        <input type="range" id="font-slider" min="16" max="42" value="{default_font}">
        <span id="font-val" style="min-width:32px;">{default_font}px</span>
        <span title="螢幕亮度">🔆</span>
        <input type="range" id="bright-slider" min="20" max="100" value="{default_bright}">
    </div>

    <div class="ctrl-group">
        <button id="toc-btn" type="button" class="btn secondary">📑 目錄</button>
        <button id="theme-btn" type="button" class="btn secondary">🎨 主題</button>
        <button id="tts-btn" type="button" class="btn secondary">🎧 朗讀</button>
    </div>
</div>

<!-- Floating Slide-Down Table of Contents Drawer -->
<div id="toc-drawer">
    <div class="toc-header">
        <span>📑 章節目錄 (點擊跳轉)</span>
        <span id="toc-close-btn" style="cursor:pointer; padding:2px 8px;">✖</span>
    </div>
    <div id="toc-list"></div>
</div>

<!-- Main Touch-to-Pause Reading Area -->
<div id="reader-viewport">
    <div id="chapters-stream"></div>
    <div id="end-banner" style="text-align:center; padding: 40px 0; opacity: 0.65; font-size: 15px; display:none;">
        🎉 全書已閱讀完畢！
    </div>
</div>

<!-- Bottom Progress Ribbon -->
<div id="bottom-ribbon">
    <span id="chap-status-label">載入中...</span>
    <span id="progress-indicator">進度: 0.0%</span>
</div>

<script>
    // Safely decode Base64 UTF-8 data from Python
    function decodeB64Utf8(b64Str) {{
        const binStr = atob(b64Str);
        const bytes = new Uint8Array(binStr.length);
        for (let i = 0; i < binStr.length; i++) {{
            bytes[i] = binStr.charCodeAt(i);
        }}
        return new TextDecoder("utf-8").decode(bytes);
    }}

    const allChapters = JSON.parse(decodeB64Utf8("{chapters_b64}"));
    const bookName = decodeB64Utf8("{book_name_b64}");
    const bookStateKey = "hongguo_v3_state_" + bookName;
    const prefsKey = "hongguo_v3_prefs";

    const cloudInitChap = {int(st.session_state.chapter_idx)};
    const cloudInitPct = {float(st.session_state.scroll_pct)};

    const topBar = document.getElementById("top-bar");
    const streamContainer = document.getElementById("chapters-stream");
    const viewport = document.getElementById("reader-viewport");
    const scrollBtn = document.getElementById("scroll-toggle-btn");
    const speedSlider = document.getElementById("speed-slider");
    const fontSlider = document.getElementById("font-slider");
    const brightSlider = document.getElementById("bright-slider");
    const tocBtn = document.getElementById("toc-btn");
    const themeBtn = document.getElementById("theme-btn");
    const ttsBtn = document.getElementById("tts-btn");
    const toast = document.getElementById("tap-toast");
    const chapLabel = document.getElementById("chap-status-label");
    const progLabel = document.getElementById("progress-indicator");
    const tocDrawer = document.getElementById("toc-drawer");
    const tocList = document.getElementById("toc-list");
    const tocCloseBtn = document.getElementById("toc-close-btn");
    const endBanner = document.getElementById("end-banner");

    // Prevent any touches/clicks on topBar or tocDrawer from bubbling to viewport
    topBar.addEventListener("click", (e) => e.stopPropagation());
    topBar.addEventListener("touchstart", (e) => e.stopPropagation(), {{passive: true}});
    tocDrawer.addEventListener("click", (e) => e.stopPropagation());
    tocDrawer.addEventListener("touchstart", (e) => e.stopPropagation(), {{passive: true}});

    // --- 1. PERSISTENT USER PREFERENCES ---
    const themes = ["theme-dark", "theme-day", "theme-green", "theme-sepia"];
    let userPrefs = {{
        speed: {default_speed},
        fontSize: {default_font},
        brightness: {default_bright},
        themeIdx: 0,
        ttsRate: {default_tts_rate}
    }};

    try {{
        const savedPrefs = localStorage.getItem(prefsKey);
        if (savedPrefs) {{
            userPrefs = Object.assign(userPrefs, JSON.parse(savedPrefs));
        }}
    }} catch (e) {{}}

    function savePrefs() {{
        try {{
            localStorage.setItem(prefsKey, JSON.stringify(userPrefs));
        }} catch (e) {{}}
    }}

    function applyPrefsToUI() {{
        speedSlider.value = userPrefs.speed;
        document.getElementById("speed-val").textContent = userPrefs.speed;
        fontSlider.value = userPrefs.fontSize;
        document.getElementById("font-val").textContent = userPrefs.fontSize + "px";
        brightSlider.value = userPrefs.brightness;
        document.getElementById("brightness-overlay").style.opacity = ((100 - userPrefs.brightness) / 100.0).toFixed(2);

        themes.forEach(t => document.body.classList.remove(t));
        const activeTheme = themes[userPrefs.themeIdx % themes.length] || "theme-dark";
        document.body.classList.add(activeTheme);

        document.querySelectorAll("p.novel-para").forEach(p => {{
            p.style.fontSize = userPrefs.fontSize + "px";
        }});
    }}

    // --- 2. CONTINUOUS INFINITE CHAPTER STREAM ENGINE ---
    let minLoadedChap = 0;
    let maxLoadedChap = -1;
    let activeChapIdx = 0;
    let isAppending = false;

    function createChapterElement(chapIdx) {{
        const ch = allChapters[chapIdx];
        const block = document.createElement("div");
        block.className = "chapter-block";
        block.id = "chap-block-" + chapIdx;
        block.dataset.chapIdx = chapIdx;

        const h = document.createElement("div");
        h.className = "chapter-heading";
        h.textContent = ch.title;
        block.appendChild(h);

        ch.paras.forEach((txt, pIdx) => {{
            const p = document.createElement("p");
            p.className = "novel-para";
            p.id = `para-${{chapIdx}}-${{pIdx}}`;
            p.dataset.chapIdx = chapIdx;
            p.dataset.paraIdx = pIdx;
            p.style.fontSize = userPrefs.fontSize + "px";
            p.textContent = txt;
            block.appendChild(p);
        }});
        return block;
    }}

    function appendNextChapterIfNeeded() {{
        if (isAppending) return;
        if (maxLoadedChap >= allChapters.length - 1) {{
            endBanner.style.display = "block";
            return;
        }}
        const distToBottom = viewport.scrollHeight - (viewport.scrollTop + viewport.clientHeight);
        if (distToBottom < 1400) {{
            isAppending = true;
            maxLoadedChap += 1;
            const nextBlock = createChapterElement(maxLoadedChap);
            streamContainer.appendChild(nextBlock);
            isAppending = false;
            if (viewport.scrollHeight - (viewport.scrollTop + viewport.clientHeight) < 900 && maxLoadedChap < allChapters.length - 1) {{
                appendNextChapterIfNeeded();
            }}
        }}
    }}

    function loadStartingFromChapter(startChapIdx, initialPct = 0.0) {{
        streamContainer.innerHTML = "";
        endBanner.style.display = "none";
        minLoadedChap = startChapIdx;
        maxLoadedChap = startChapIdx - 1;
        activeChapIdx = startChapIdx;

        const countToRender = Math.min(2, allChapters.length - startChapIdx);
        for (let i = 0; i < countToRender; i++) {{
            maxLoadedChap += 1;
            streamContainer.appendChild(createChapterElement(maxLoadedChap));
        }}
        if (maxLoadedChap >= allChapters.length - 1) {{
            endBanner.style.display = "block";
        }}

        setTimeout(() => {{
            const firstBlock = document.getElementById("chap-block-" + startChapIdx);
            if (firstBlock && initialPct > 0) {{
                viewport.scrollTop = firstBlock.offsetTop + (firstBlock.offsetHeight * (initialPct / 100.0));
            }} else {{
                viewport.scrollTop = 0;
            }}
            updateReadingProgress();
        }}, 50);
    }}

    // Build Floating TOC Drawer Items
    function buildTocDrawer() {{
        tocList.innerHTML = "";
        allChapters.forEach((ch, idx) => {{
            const item = document.createElement("div");
            item.className = "toc-item" + (idx === activeChapIdx ? " active-chap" : "");
            item.id = "toc-item-" + idx;
            item.textContent = `${{idx + 1}}. ${{ch.title}}`;
            item.addEventListener("click", (e) => {{
                e.stopPropagation();
                tocDrawer.style.display = "none";
                loadStartingFromChapter(idx, 0.0);
                showToast(`📑 已跳轉至：${{ch.title}}`);
            }});
            tocList.appendChild(item);
        }});
    }}

    function toggleTocDrawer() {{
        if (tocDrawer.style.display === "block") {{
            tocDrawer.style.display = "none";
        }} else {{
            document.querySelectorAll(".toc-item").forEach(el => el.classList.remove("active-chap"));
            const activeItem = document.getElementById("toc-item-" + activeChapIdx);
            if (activeItem) activeItem.classList.add("active-chap");
            tocDrawer.style.display = "block";
            if (activeItem) activeItem.scrollIntoView({{ block: "center" }});
        }}
    }}

    tocBtn.addEventListener("click", (e) => {{
        e.stopPropagation();
        toggleTocDrawer();
    }});
    tocCloseBtn.addEventListener("click", (e) => {{
        e.stopPropagation();
        tocDrawer.style.display = "none";
    }});

    // Track current visible chapter & percentage inside that chapter
    function updateReadingProgress() {{
        const blocks = document.querySelectorAll(".chapter-block");
        const probeY = viewport.scrollTop + 120;
        let currentBlock = blocks[0];

        for (let i = 0; i < blocks.length; i++) {{
            if (blocks[i].offsetTop <= probeY) {{
                currentBlock = blocks[i];
            }} else {{
                break;
            }}
        }}

        if (currentBlock) {{
            activeChapIdx = parseInt(currentBlock.dataset.chapIdx, 10);
            const chapTitle = allChapters[activeChapIdx]?.title || "";
            const blockScroll = Math.max(0, probeY - currentBlock.offsetTop);
            const chapPct = Math.min(100, Math.max(0, (blockScroll / Math.max(1, currentBlock.offsetHeight)) * 100)).toFixed(1);

            chapLabel.textContent = `📖 (${{activeChapIdx + 1}}/${{allChapters.length}}) ${{chapTitle}}`;
            progLabel.textContent = `本章進度: ${{chapPct}}%`;

            try {{
                localStorage.setItem(bookStateKey, JSON.stringify({{
                    chapIdx: activeChapIdx,
                    pct: parseFloat(chapPct)
                }}));
            }} catch (e) {{}}
        }}
    }}

    // --- 3. INITIALIZE BOOK POSITION ---
    function initReader() {{
        applyPrefsToUI();
        buildTocDrawer();

        let startChap = cloudInitChap;
        let startPct = cloudInitPct;

        try {{
            const savedBookState = localStorage.getItem(bookStateKey);
            if (savedBookState) {{
                const parsed = JSON.parse(savedBookState);
                if (typeof parsed.chapIdx === "number" && parsed.chapIdx >= 0 && parsed.chapIdx < allChapters.length) {{
                    startChap = parsed.chapIdx;
                    startPct = parsed.pct || 0.0;
                }}
            }}
        }} catch (e) {{}}

        loadStartingFromChapter(startChap, startPct);
    }}

    // --- 4. SUB-PIXEL SMOOTH AUTOSCROLL ENGINE ---
    let isScrolling = false;
    let pixelAccumulator = 0;
    let lastFrameTime = null;

    function autoScrollStep(timestamp) {{
        if (!isScrolling) return;
        if (!lastFrameTime) lastFrameTime = timestamp;
        const deltaMs = timestamp - lastFrameTime;
        lastFrameTime = timestamp;

        const pxPerSec = 6 + Math.pow(userPrefs.speed, 1.35) * 0.42;
        pixelAccumulator += (pxPerSec * deltaMs) / 1000.0;

        if (pixelAccumulator >= 1.0) {{
            const step = Math.floor(pixelAccumulator);
            viewport.scrollTop += step;
            pixelAccumulator -= step;
            appendNextChapterIfNeeded();
        }}

        if (maxLoadedChap >= allChapters.length - 1 && (viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 3)) {{
            setScrollingState(false, "🎉 已到達全書結尾");
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
            showToast(customMsg || "▶️ 開始無縫滾動");
            requestAnimationFrame(autoScrollStep);
        }} else {{
            scrollBtn.innerHTML = "▶️ 自動滾動";
            showToast(customMsg || "⏸ 已暫停滾動");
        }}
    }}

    scrollBtn.addEventListener("click", (e) => {{
        e.stopPropagation();
        setScrollingState(!isScrolling);
    }});

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
            if (isScrolling) {{
                setScrollingState(false, "⏸ 手動滑動已暫停自動滾動");
            }}
        }}
    }}, {{passive: true}});

    viewport.addEventListener("click", (e) => {{
        if (tocDrawer.style.display === "block") {{
            tocDrawer.style.display = "none";
            return;
        }}
        if (isDraggingTouch) return;
        setScrollingState(!isScrolling);
    }});

    window.addEventListener("keydown", (e) => {{
        if (e.code === "Space") {{
            e.preventDefault();
            setScrollingState(!isScrolling);
        }}
    }});

    viewport.addEventListener("scroll", () => {{
        appendNextChapterIfNeeded();
        updateReadingProgress();
    }});

    // --- 5. LIVE UI SLIDERS & THEME BUTTON ---
    speedSlider.addEventListener("input", (e) => {{
        userPrefs.speed = parseInt(e.target.value, 10);
        document.getElementById("speed-val").textContent = userPrefs.speed;
        savePrefs();
    }});

    fontSlider.addEventListener("input", (e) => {{
        userPrefs.fontSize = parseInt(e.target.value, 10);
        document.getElementById("font-val").textContent = userPrefs.fontSize + "px";
        document.querySelectorAll("p.novel-para").forEach(p => {{
            p.style.fontSize = userPrefs.fontSize + "px";
        }});
        savePrefs();
    }});

    brightSlider.addEventListener("input", (e) => {{
        userPrefs.brightness = parseInt(e.target.value, 10);
        const overlay = document.getElementById("brightness-overlay");
        overlay.style.opacity = ((100 - userPrefs.brightness) / 100.0).toFixed(2);
        savePrefs();
    }});

    themeBtn.addEventListener("click", (e) => {{
        e.stopPropagation();
        document.body.classList.remove(themes[userPrefs.themeIdx % themes.length]);
        userPrefs.themeIdx = (userPrefs.themeIdx + 1) % themes.length;
        document.body.classList.add(themes[userPrefs.themeIdx]);
        savePrefs();
    }});

    // --- 6. CONTINUOUS CROSS-CHAPTER READ ALOUD (TTS) ---
    let isSpeaking = false;
    const synth = window.speechSynthesis;

    function getAllRenderedParagraphs() {{
        return Array.from(document.querySelectorAll("p.novel-para"));
    }}

    function findFirstVisibleParagraphElement() {{
        const viewTop = viewport.scrollTop + 40;
        const allParas = getAllRenderedParagraphs();
        for (let i = 0; i < allParas.length; i++) {{
            const el = allParas[i];
            if ((el.offsetTop + el.offsetHeight) > viewTop) {{
                return i;
            }}
        }}
        return 0;
    }}

    function speakRenderedParagraphAt(idx) {{
        if (!isSpeaking) return;
        appendNextChapterIfNeeded();
        const allParas = getAllRenderedParagraphs();
        if (idx >= allParas.length) {{
            stopTTS();
            return;
        }}
        allParas.forEach(p => p.classList.remove("speaking-active"));
        const activeEl = allParas[idx];
        activeEl.classList.add("speaking-active");
        activeEl.scrollIntoView({{ behavior: "smooth", block: "center" }});

        const text = activeEl.textContent;
        const utter = new SpeechSynthesisUtterance(text);
        const hasChinese = /[\\u4e00-\\u9fa5]/.test(text);
        utter.lang = hasChinese ? "zh-HK" : "en-US";
        utter.rate = userPrefs.ttsRate || 1.1;

        utter.onend = () => {{
            if (isSpeaking) {{
                speakRenderedParagraphAt(idx + 1);
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

    ttsBtn.addEventListener("click", (e) => {{
        e.stopPropagation();
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
            const startIdx = findFirstVisibleParagraphElement();
            showToast("🎧 開始跨章連續語音朗讀");
            synth.cancel();
            speakRenderedParagraphAt(startIdx);
        }}
    }});

    initReader();
</script>
</body>
</html>
"""

components.html(reader_html, height=820, scrolling=False)
