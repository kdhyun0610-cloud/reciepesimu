"""도마뱀 관리 — 실행: streamlit run lizard.py
설치: pip install -r lizard.txt
Streamlit Cloud 배포 시 lizard.txt를 requirements.txt로 이름 변경하세요.
기본 저장은 실행 폴더의 lizard_data.json (Cloud 재시작 시 유실 가능).
영구 저장: Streamlit Secrets에 다음을 설정하세요. 공개 앱에는 사용 비밀번호 권장.
APP_PASSWORD = "사용할 비밀번호"
[github]
token = "Contents 읽기/쓰기 권한을 가진 fine-grained token"
repo = "계정/저장소"
branch = "main"
path = "data/lizard_data.json"
개인 기록은 비공개 저장소 권장. 토큰은 코드/저장소에 올리지 마세요.
"""
import base64
import copy
import hmac
import html
import json
import os
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote
import streamlit as st

st.set_page_config(page_title="도마뱀 관리", page_icon="🦎", layout="wide")
LOCAL = Path(__file__).with_name("lizard_data.json")

def today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()

def empty_data():
    return {"version": 1, "items": []}

def validate(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
        raise ValueError("지원하지 않는 백업 형식입니다.")
    ids, nums = set(), {"lizard": set(), "supplies": set()}
    for x in data["items"]:
        if not isinstance(x, dict) or x.get("kind") not in nums:
            raise ValueError("창 종류가 올바르지 않습니다.")
        if not isinstance(x.get("id"), str) or not x["id"] or x["id"] in ids:
            raise ValueError("창 ID가 올바르지 않거나 중복됩니다.")
        ids.add(x["id"])
        n = x.get("number")
        if type(n) is not int or n < 1 or n > 999999 or n in nums[x["kind"]]:
            raise ValueError("같은 종류의 창 번호는 1~999999 사이이며 중복될 수 없습니다.")
        nums[x["kind"]].add(n)
        if not isinstance(x.get("name"), str) or not x["name"].strip() or len(x["name"]) > 60:
            raise ValueError("이름은 1~60자로 입력하세요.")
        if not isinstance(x.get("features"), list) or any(not isinstance(s, str) or not s.strip() or len(s) > 3000 for s in x["features"]):
            raise ValueError("특징은 항목당 1~3000자여야 합니다.")
        if type(x.get("interval")) is not int or not 1 <= x["interval"] <= 365:
            raise ValueError("주기는 1~365일이어야 합니다.")
        date.fromisoformat(x["anchor"])
    return data

def remaining(item, current=None):
    elapsed = max(0, ((current or today()) - date.fromisoformat(item["anchor"])).days)
    return item["interval"] - elapsed % (item["interval"] + 1)

def gh_config():
    return dict(st.secrets.get("github", {}))

def remote(cfg, method="GET", payload=None):
    repo = cfg["repo"]
    branch = cfg.get("branch", "main")
    path = cfg.get("path", "data/lizard_data.json")
    url = f"https://api.github.com/repos/{quote(repo, safe='/')}/contents/{quote(path, safe='/')}"
    if method == "GET":
        url += "?ref=" + quote(branch, safe="")
    req = Request(url, data=None if payload is None else json.dumps(payload).encode(), method=method,
                  headers={"Authorization": "Bearer " + cfg["token"], "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "LizardCare"})
    with urlopen(req, timeout=20) as response:
        return json.load(response)

def load_data():
    cfg = gh_config()
    if cfg:
        try:
            result = remote(cfg)
            return validate(json.loads(base64.b64decode(result["content"]))), result["sha"]
        except HTTPError as e:
            if e.code == 404:
                # 404 can also mean a wrong repo or missing permission; saving will expose that error.
                return empty_data(), None
            raise RuntimeError(f"GitHub 읽기 실패 (HTTP {e.code}). 권한과 Secrets를 확인하세요.") from e
    if LOCAL.exists():
        raw = LOCAL.read_bytes()
        return validate(json.loads(raw)), base64.b64encode(raw).decode()
    return empty_data(), None

def persist(data):
    validate(data)
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
    cfg = gh_config()
    if cfg:
        body = {"message": "Update lizard care records", "content": base64.b64encode(raw).decode(), "branch": cfg.get("branch", "main")}
        if st.session_state.revision:
            body["sha"] = st.session_state.revision
        try:
            result = remote(cfg, "PUT", body)
        except HTTPError as e:
            if e.code in (409, 422):
                raise RuntimeError("다른 기기에서 기록이 변경되었을 수 있습니다. 왼쪽의 ‘저장소 새로고침’ 후 다시 수정하세요.") from e
            raise RuntimeError(f"GitHub 저장 실패 (HTTP {e.code}). 토큰 권한·저장소·브랜치를 확인하세요.") from e
        st.session_state.revision = result["content"]["sha"]
    else:
        current = base64.b64encode(LOCAL.read_bytes()).decode() if LOCAL.exists() else None
        if current != st.session_state.revision:
            raise RuntimeError("저장 파일이 변경되었습니다. ‘저장소 새로고침’ 후 다시 시도하세요.")
        tmp = LOCAL.with_suffix('.tmp')
        tmp.write_bytes(raw)
        os.replace(tmp, LOCAL)
        st.session_state.revision = base64.b64encode(raw).decode()
    st.session_state.data = data

def commit(data, message):
    try:
        persist(data)
    except Exception as e:
        st.error(str(e))
        return False
    st.session_state.flash = message
    st.rerun()

def changed_item(item_id, **updates):
    data = copy.deepcopy(st.session_state.data)
    for x in data["items"]:
        if x["id"] == item_id:
            x.update(updates)
    return data

def number_free(kind, number, exclude=None):
    return all(x["id"] == exclude or x["kind"] != kind or x["number"] != number for x in st.session_state.data["items"])

st.markdown("""<style>
.stApp {background:#f7f8ef;color:#203e32}
.block-container {max-width:1180px;padding-top:2rem}
h1,h2,h3 {color:#254c39!important}
.stButton>button {border-radius:16px;border:1px solid #a9c5ab;min-height:3rem}
.stButton>button:hover {border-color:#458459;color:#254c39}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius:20px}
.hero {text-align:center;padding:12px 0 20px}.hero p {color:#597360}
.pet {font-size:112px;text-align:center;margin:10px 0;line-height:1.5}
</style>""", unsafe_allow_html=True)
try:
    password = st.secrets.get("APP_PASSWORD", "")
except Exception:
    password = ""
# Missing secrets.toml must not prevent local first launch.
_original_gh_config = gh_config

def gh_config():
    try:
        return _original_gh_config()
    except FileNotFoundError:
        return {}

if password and not st.session_state.get("authorized"):
    st.title("🦎 우리집 도마뱀 관리")
    with st.form("login"):
        typed = st.text_input("관리 비밀번호", type="password")
        if st.form_submit_button("들어가기"):
            if hmac.compare_digest(typed.encode(), str(password).encode()):
                st.session_state.authorized = True
                st.rerun()
            st.error("비밀번호를 확인해주세요.")
    st.stop()

if "data" not in st.session_state:
    try:
        st.session_state.data, st.session_state.revision = load_data()
    except Exception as e:
        st.error(f"기록을 불러오지 못했습니다: {e}")
        st.info("기존 데이터를 보호하기 위해 편집을 중단했습니다. 설정을 확인한 뒤 새로고침하세요.")
        st.stop()
if "selected" not in st.session_state:
    st.session_state.selected = None
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

with st.sidebar:
    st.title("🦎 작은 사육장")
    st.caption("한국 시간 기준 · 내 친구들의 기록")
    if st.button("🏠 전체 창 보기", use_container_width=True):
        st.session_state.selected = None
        st.rerun()
    if st.button("↻ 저장소 새로고침", use_container_width=True):
        try:
            fresh, rev = load_data()
            st.session_state.data, st.session_state.revision = fresh, rev
            st.rerun()
        except Exception as e:
            st.error(str(e))
    st.caption("저장 방식: " + ("GitHub 영구 저장" if gh_config() else "실행 폴더 저장"))
    if not gh_config():
        st.warning("Cloud에서는 재시작·재배포 시 기록이 사라질 수 있습니다. GitHub 저장을 설정하거나 백업하세요.")
    st.download_button("💾 전체 기록 백업", json.dumps(st.session_state.data, ensure_ascii=False, indent=2), file_name=f"lizard_backup_{today()}.json", mime="application/json", use_container_width=True)
    with st.expander("백업 복원"):
        uploaded = st.file_uploader("JSON 백업", type="json")
        confirm = st.checkbox("현재 기록을 백업 내용으로 덮어씁니다")
        if st.button("백업 복원하기", disabled=not (uploaded and confirm)):
            try:
                restored = validate(json.loads(uploaded.getvalue()))
            except Exception as e:
                st.error(f"백업 오류: {e}")
            else:
                commit(restored, "백업을 복원했습니다.")
    with st.expander("배포 · 저장 안내"):
        st.markdown("""**로컬 실행**\n```bash\npip install -r lizard.txt\nstreamlit run lizard.py\n```\n**Streamlit Community Cloud**\n1. `lizard.py`와 의존성 파일을 GitHub에 올립니다.\n2. `lizard.txt`는 **requirements.txt**로 이름을 바꿉니다.\n3. 메인 파일을 `lizard.py`로 지정합니다.\n4. 영구 저장은 아래 Secrets를 설정합니다. 토큰은 Contents 읽기/쓰기 권한이 필요합니다. 비공개 데이터 저장소도 사용할 수 있습니다.\n```toml\nAPP_PASSWORD = "나만의 비밀번호"\n[github]\ntoken = "토큰은 여기에만 입력"\nrepo = "계정/저장소"\nbranch = "main"\npath = "data/lizard_data.json"\n```\n공개 앱에는 비밀번호를 설정하세요. 비밀번호를 공유하면 모든 사용자가 같은 기록을 수정합니다. GitHub 저장소와 브랜치는 먼저 생성되어 있어야 합니다. 자동 반복은 생물의 실제 상태와 무관한 달력 표시이며, 앱이 닫혀 있을 때 알림을 보내지 않습니다.""")

items = st.session_state.data["items"]
selected = next((x for x in items if x["id"] == st.session_state.selected), None)
if selected is None:
    left, middle, right = st.columns([1, 2, 1])
    with middle:
        if st.button("＋", key="big_add", use_container_width=True, help="도마뱀 또는 준비물 창을 추가합니다"):
            st.session_state.add_open = not st.session_state.get("add_open", False)
        st.markdown('<div class="hero"><h1>도마뱀 관리</h1><p>작은 친구들의 하루를 차곡차곡 기록해요.</p></div>', unsafe_allow_html=True)
    # Target only the central add button, not every button in the app.
    st.markdown('<style>.st-key-big_add button{font-size:64px!important;min-height:110px;background:#e4efd9}</style>', unsafe_allow_html=True)
    if st.session_state.get("add_open"):
        with st.form("new_item", clear_on_submit=True):
            kind_label = st.selectbox("창 종류", ["도마뱀", "준비물"])
            name = st.text_input("이름", max_chars=60, placeholder="예: 핑키 / 먹이와 환경")
            num = st.number_input("번호 (같은 종류 안에서 중복 불가)", min_value=1, max_value=999999, step=1)
            if st.form_submit_button("새 창 만들기", type="primary"):
                kind = "lizard" if kind_label == "도마뱀" else "supplies"
                if not name.strip():
                    st.error("이름을 입력해주세요.")
                elif not number_free(kind, int(num)):
                    st.error("이미 사용 중인 번호입니다. 다른 번호를 입력해주세요.")
                else:
                    data = copy.deepcopy(st.session_state.data)
                    data["items"].append({"id": uuid4().hex, "kind": kind, "name": name.strip(), "number": int(num), "features": [], "interval": 3, "anchor": today().isoformat()})
                    commit(data, "새 창을 만들었습니다.")
    for tab, kind in zip(st.tabs(["🦎 도마뱀", "🧺 준비물"]), ["lizard", "supplies"]):
        with tab:
            group = sorted([x for x in items if x["kind"] == kind], key=lambda x: x["number"])
            if not group:
                st.info("위의 ＋ 버튼을 눌러 첫 창을 만들어주세요.")
            cols = st.columns(3)
            for i, x in enumerate(group):
                with cols[i % 3]:
                    with st.container(border=True):
                        st.subheader(f"{'🦎' if kind == 'lizard' else '🧺'} {x['number']}. {x['name']}")
                        d = remaining(x)
                        st.caption(("급여" if kind == "lizard" else "준비물 점검") + " · " + ("D-Day" if d == 0 else f"D-{d}"))
                        st.caption(f"기록 {len(x['features'])}개")
                        if st.button("창 열기 →", key="open_" + x["id"], use_container_width=True):
                            st.session_state.selected = x["id"]
                            st.rerun()
else:
    x = selected
    title_col, schedule_col = st.columns([3, 2])
    with title_col:
        if st.button("← 전체 창"):
            st.session_state.selected = None
            st.rerun()
        st.title(f"{x['number']}. {x['name']}")
        st.caption("나만의 도마뱀 기록" if x["kind"] == "lizard" else "먹이 · 환경 · 사육 준비물 기록")
    with schedule_col:
        with st.container(border=True):
            st.markdown("**🍽 먹이 일정**" if x["kind"] == "lizard" else "**🧺 준비물 점검 일정**")
            a, b = st.columns([2, 1])
            d = remaining(x)
            a.metric("다음 일정까지", "D-Day" if d == 0 else f"D-{d}")
            if b.button("↻ 리셋", help="오늘을 시작일로 지정해 D-N으로 돌아갑니다."):
                commit(changed_item(x["id"], anchor=today().isoformat()), "일정을 리셋했습니다.")
            with st.expander("일정 설정"):
                with st.form("schedule_" + x["id"]):
                    period = st.number_input("D-N의 N (일)", min_value=1, max_value=365, value=x["interval"])
                    anchor = st.date_input("D-N으로 시작하는 날짜", value=date.fromisoformat(x["anchor"]), max_value=today())
                    st.caption("예: N=3이면 D-3 → D-2 → D-1 → D-Day → D-3. 실제 간격은 4일입니다. 저장하면 선택한 시작일로 재계산합니다.")
                    if st.form_submit_button("일정 저장"):
                        commit(changed_item(x["id"], interval=int(period), anchor=anchor.isoformat()), "일정을 저장했습니다.")
    st.markdown('<div class="pet" role="img" aria-label="귀여운 도마뱀">🦎</div>' if x["kind"] == "lizard" else '<div class="pet" role="img" aria-label="준비물 바구니">🧺</div>', unsafe_allow_html=True)
    st.subheader("특징과 관찰 기록" if x["kind"] == "lizard" else "준비물과 환경 요건")
    with st.form("feature_add_" + x["id"], clear_on_submit=True):
        text = st.text_area("새 항목", max_chars=3000, placeholder="한 번 저장할 때마다 번호가 붙은 항목으로 추가됩니다.")
        if st.form_submit_button("＋ 항목 추가", type="primary"):
            if text.strip():
                commit(changed_item(x["id"], features=x["features"] + [text.strip()]), "항목을 추가했습니다.")
            else:
                st.error("내용을 입력해주세요.")
    for i, text in enumerate(x["features"]):
        with st.container(border=True):
            st.text(f"{i + 1}. {text}")
            with st.expander("✏️ 수정 / 삭제"):
                with st.form(f"edit_{x['id']}_{i}"):
                    edited = st.text_area("내용 수정", value=text, max_chars=3000)
                    save, delete = st.columns(2)
                    update = save.form_submit_button("수정 저장")
                    remove = delete.form_submit_button("이 항목 삭제")
                    if update or remove:
                        features = list(x["features"])
                        if remove:
                            features.pop(i)
                        elif not edited.strip():
                            st.error("빈 내용은 저장할 수 없습니다.")
                            continue
                        else:
                            features[i] = edited.strip()
                        commit(changed_item(x["id"], features=features), "기록을 변경했습니다.")
    with st.expander("⚙️ 이름 · 번호 수정"):
        with st.form("profile_" + x["id"]):
            name = st.text_input("이름", value=x["name"], max_chars=60)
            number = st.number_input("번호", min_value=1, max_value=999999, value=x["number"])
            if st.form_submit_button("변경 저장"):
                if not name.strip():
                    st.error("이름을 입력해주세요.")
                elif not number_free(x["kind"], int(number), x["id"]):
                    st.error("이미 사용 중인 번호입니다.")
                else:
                    commit(changed_item(x["id"], name=name.strip(), number=int(number)), "이름과 번호를 수정했습니다. 목록은 번호순으로 정렬됩니다.")
    with st.expander("🗑️ 이 창 삭제"):
        sure = st.checkbox("이 창과 안의 모든 기록을 삭제합니다", key="confirm_" + x["id"])
        if st.button("영구 삭제", disabled=not sure):
            data = copy.deepcopy(st.session_state.data)
            data["items"] = [it for it in data["items"] if it["id"] != x["id"]]
            commit(data, "창을 삭제했습니다.")
    st.caption("화면의 날짜 표시는 앱 조작 또는 새로고침 시 갱신됩니다. 급여 주기는 종·나이·건강에 맞춰 직접 설정해주세요.")
