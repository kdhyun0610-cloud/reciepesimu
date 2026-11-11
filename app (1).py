"""레시피룸 · Streamlit 조리 순서 시뮬레이터

실행: pip install -r requirements.txt && streamlit run app.py
Python 3.10+ 권장. 저장소에 app.py와 requirements.txt 두 파일을 올리세요.

선택 설정: Streamlit Community Cloud > App settings > Secrets에 아래 내용을 넣습니다.
실제 토큰을 코드/GitHub에 올리지 마세요. 아래 값은 예시이며 그대로 쓰면 안 됩니다.

[github]
token = "비공개 데이터 저장소에만 Contents 읽기/쓰기를 허용한 fine-grained token"
repo = "계정/비공개-데이터저장소"
branch = "main"
folder = "recipes"

github 설정: 미리 초기 커밋을 만든 별도 비공개 데이터 저장소 사용 권장.
영구 저장은 이 설정이 있어야 합니다. 없으면 로컬 SQLite를 쓰므로 Cloud 재배포/
휴면 복구 때 사라질 수 있습니다. GitHub 쓰기 실패 시 성공으로 표시하지 않습니다.
이 앱은 개인용/신뢰하는 사용자끼리 쓰는 공유 보관함입니다. 모든 접속자가 같은
기록을 보고 삭제할 수 있으므로 Streamlit 접근 권한을 비공개로 제한하세요.
삭제는 현재 저장본 제거이며 GitHub 커밋 이력까지 지우는 보안 삭제가 아닙니다.
단계별 재료·기구·동작을 직접 선택합니다. 문장 해석이나 AI 호출은 없습니다.
화면은 선택한 동작의 도식 애니메이션이며 맛/온도/식품 안전을 예측하지 않습니다.
"""
from __future__ import annotations
import copy
import html
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="레시피룸 · 나만의 조리 기록", page_icon="🍳", layout="wide")

ACTIONS = {
    "자르기": ["자르", "잘라", "썰", "썬", "썰어"],
    "다지기": ["다지", "다져"], "넣기": ["넣", "투입", "추가"],
    "섞기": ["섞", "저어", "젓", "혼합"], "볶기": ["볶"],
    "굽기": ["굽", "구워", "구운"], "끓이기": ["끓"],
    "삶기": ["삶"], "찌기": ["찌고", "찌기", "찐다", "쪄", "찐"],
    "튀기기": ["튀기", "튀겨"], "데우기": ["데우", "데워", "가열"],
    "뒤집기": ["뒤집"], "붓기": ["붓", "부어"], "담기": ["담", "플레이팅"],
    "식히기": ["식히", "식혀"], "기다리기": ["기다", "휴지", "재워", "재우"],
    "꺼내기": ["꺼내", "건져"], "무치기": ["무치", "무쳐"],
}
EMOJI = {"계란":"🥚", "달걀":"🥚", "양파":"🧅", "당근":"🥕", "감자":"🥔", "토마토":"🍅", "닭":"🍗", "고기":"🥩", "돼지":"🥩", "소고기":"🥩", "밥":"🍚", "쌀":"🌾", "물":"💧", "우유":"🥛", "치즈":"🧀", "버섯":"🍄", "브로콜리":"🥦", "오이":"🥒", "마늘":"🧄", "고추":"🌶️", "새우":"🦐", "생선":"🐟", "빵":"🍞", "소금":"🧂", "버터":"🧈", "팬":"🍳", "냄비":"🍲", "칼":"🔪", "도마":"🪵", "접시":"🍽️", "그릇":"🥣", "볼":"🥣", "오븐":"♨️", "전자레인지":"♨️", "숟가락":"🥄", "젓가락":"🥢", "가위":"✂️"}
COMMON_FOOD = ["계란", "달걀", "양파", "당근", "감자", "토마토", "닭고기", "돼지고기", "소고기", "밥", "쌀", "물", "우유", "치즈", "버섯", "브로콜리", "오이", "마늘", "고추", "새우", "생선", "빵", "소금", "버터", "설탕", "간장", "식용유", "후추", "대파", "김치", "두부", "밀가루"]
COMMON_TOOLS = ["프라이팬", "냄비", "칼", "도마", "접시", "그릇", "볼", "오븐", "전자레인지", "숟가락", "젓가락", "가위", "에어프라이어", "믹서기"]
HOT = {"볶기", "굽기", "끓이기", "삶기", "찌기", "튀기기", "데우기"}


def cfg(section):
    try:
        return dict(st.secrets.get(section, {}))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return {}


def now():
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def icon(name):
    return next((v for k, v in EMOJI.items() if k in name), "🥬")


def names(text):
    result = list(dict.fromkeys(x.strip() for x in re.split(r"[,，\n]", text) if x.strip()))
    if len(result) > 20 or any(len(x) > 24 for x in result):
        raise ValueError("항목은 각각 24자 이하, 재료·기구는 각각 20개 이하로 입력해 주세요.")
    if any(re.search(r"[<>\x00-\x1f]", x) for x in result):
        raise ValueError("항목 이름에 꺾쇠나 제어 문자는 사용할 수 없습니다.")
    return result


class Store:
    def __init__(self):
        self.g = cfg("github")
        if self.g and not all(self.g.get(k) for k in ("token", "repo")):
            raise ValueError("GitHub 설정에 token과 repo가 모두 필요합니다.")
        self.remote = bool(self.g)
        if not self.remote:
            with self.db() as c:
                c.execute("CREATE TABLE IF NOT EXISTS recipes (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def db(self):
        return sqlite3.connect(str(Path(__file__).with_name("recipes.sqlite3")), timeout=20)

    def url(self, rid=None):
        folder = self.g.get("folder", "recipes").strip("/")
        path = folder + ("/" + rid + ".json" if rid else "")
        return f"https://api.github.com/repos/{self.g['repo']}/contents/{quote(path, safe='/')}"

    def request(self, method, rid=None, **kw):
        r = requests.request(method, self.url(rid), headers={"Authorization": f"Bearer {self.g['token']}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}, timeout=25, **kw)
        if r.status_code not in (200, 201, 404):
            raise RuntimeError(f"영구 저장소 요청 실패 (HTTP {r.status_code}). 권한·저장소·브랜치 설정을 확인하세요.")
        return r

    def read_remote(self, rid):
        r = self.request("GET", rid, params={"ref": self.g.get("branch", "main")})
        if r.status_code == 404:
            return None, None
        import base64
        body = r.json()
        return json.loads(base64.b64decode(body["content"]).decode("utf-8")), body["sha"]

    def all(self):
        if not self.remote:
            with self.db() as c:
                rows = [json.loads(r[0]) for r in c.execute("SELECT payload FROM recipes")]
        else:
            r = self.request("GET", params={"ref": self.g.get("branch", "main")})
            if r.status_code == 404:
                # Missing folder is normal only if the repository and branch are readable.
                check = requests.get(f"https://api.github.com/repos/{self.g['repo']}/branches/{quote(self.g.get('branch', 'main'), safe='')}", headers={"Authorization": f"Bearer {self.g['token']}"}, timeout=25)
                if check.status_code != 200:
                    raise RuntimeError("데이터 저장소 또는 브랜치를 읽을 수 없습니다. 초기 커밋과 권한을 확인하세요.")
                return []
            rows = []
            for f in r.json():
                if f.get("type") == "file" and re.fullmatch(r"[a-f0-9]{32}\.json", f["name"]):
                    item, _ = self.read_remote(f["name"][:-5])
                    if item:
                        rows.append(item)
        return sorted(rows, key=lambda x: x["updated_at"], reverse=True)

    def save(self, item):
        raw = json.dumps(item, ensure_ascii=False)
        if not self.remote:
            with self.db() as c:
                c.execute("INSERT OR REPLACE INTO recipes VALUES (?, ?)", (item["id"], raw))
        else:
            import base64
            old, sha = self.read_remote(item["id"])
            # Do not overwrite a newer copy opened by another tab/user.
            if old and old.get("revision", 0) != item.get("revision", 0) - 1:
                raise RuntimeError("다른 창에서 기록이 변경되었습니다. 보관함에서 최신 기록을 다시 열어 주세요.")
            data = {"message": "Save recipe simulation", "content": base64.b64encode(raw.encode()).decode(), "branch": self.g.get("branch", "main")}
            if sha:
                data["sha"] = sha
            r = self.request("PUT", item["id"], json=data)
            if r.status_code == 404:
                raise RuntimeError("영구 저장에 실패했습니다. 저장소 쓰기 권한을 확인하세요.")

    def delete(self, item):
        if not self.remote:
            with self.db() as c:
                c.execute("DELETE FROM recipes WHERE id = ?", (item["id"],))
        else:
            old, sha = self.read_remote(item["id"])
            if not sha:
                return
            if old.get("revision") != item.get("revision"):
                raise RuntimeError("기록이 변경되었습니다. 최신 기록을 다시 열어 삭제해 주세요.")
            r = self.request("DELETE", item["id"], json={"message":"Delete recipe simulation", "sha":sha, "branch":self.g.get("branch", "main")})
            if r.status_code == 404:
                raise RuntimeError("삭제 요청에 실패했습니다.")


def validate_steps(steps, foods, tools):
    if not steps or len(steps) > 30:
        raise ValueError("조리 단계는 1~30개로 구성해 주세요.")
    for i, step in enumerate(steps, 1):
        if step["action"] not in ACTIONS:
            raise ValueError(f"{i}단계의 동작을 선택해 주세요.")
        if not step["ingredients"] or not step["tools"]:
            raise ValueError(f"{i}단계의 재료와 기구를 각각 한 개 이상 선택해 주세요.")
        if any(x not in foods for x in step["ingredients"]) or any(x not in tools for x in step["tools"]):
            raise ValueError(f"{i}단계의 재료·기구가 현재 조리대에 없습니다. 다시 선택해 주세요.")
    return copy.deepcopy(steps)


def scene(foods, tools, steps=None):
    payload = json.dumps({"foods":[{"name":n,"icon":icon(n)} for n in foods], "tools":[{"name":n,"icon":icon(n)} for n in tools], "steps":steps or []}, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    document = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
    *{box-sizing:border-box}body{margin:0;font:14px system-ui,sans-serif;color:#254330;background:#f0f5f0}.wrap{padding:16px;border-radius:18px;border:1px solid #d3e2d5}.top{display:flex;justify-content:space-between}.bench{position:relative;height:300px;margin:12px 0;background:linear-gradient(#f9fbf7 75%,#e8d9bd 75%);border-radius:14px;overflow:hidden}.food{position:absolute;width:70px;text-align:center;transition:left .6s,top .6s;z-index:3}.pic{display:block;font-size:32px}.name{font-size:11px;overflow-wrap:anywhere;display:block}.status{font-size:10px;color:#53735d}.vessel{position:absolute;width:94px;text-align:center;top:210px;transform:translateX(-50%);border-radius:10px;padding:3px}.vessel.chosen{background:#cce5ce;outline:2px solid #60916e}.vessel .pic{font-size:36px}.effect{position:absolute;top:60px;left:50%;transform:translateX(-50%);font-size:30px;z-index:5;pointer-events:none}.badge{text-align:center;font-weight:700;color:#306041;min-height:24px}#caption{min-height:44px;white-space:pre-wrap;overflow-wrap:anywhere;text-align:center}button{border:0;background:#206648;color:white;border-radius:8px;padding:9px 12px;margin:3px;cursor:pointer}button:disabled{opacity:.4}.controls{text-align:center}input{width:100%;accent-color:#206648}.note{font-size:11px;color:#5c7161;text-align:center}.chop .pic{animation:chop .3s infinite alternate}.mix .pic{animation:mix .9s linear infinite}.flip .pic{animation:flip 1s infinite}.heat .pic{animation:heat .45s infinite alternate}.pour .pic{animation:pour 1.2s infinite}.move .pic{animation:bob .8s infinite alternate}.cool .pic{animation:cool 1.2s infinite alternate}.wait .pic{animation:wait 1.4s infinite alternate}.effect.steam{animation:steam 1.2s infinite}.effect.knife{animation:chop .3s infinite alternate}@keyframes chop{to{transform:translateY(12px) rotate(-20deg)}}@keyframes mix{to{transform:rotate(360deg)}}@keyframes flip{50%{transform:translateY(-30px) rotate(180deg)}100%{transform:rotate(360deg)}}@keyframes heat{to{transform:translateY(-6px);filter:sepia(.65) saturate(1.8)}}@keyframes pour{50%{transform:translateX(18px) rotate(55deg)}}@keyframes bob{to{transform:translateY(-12px)}}@keyframes cool{to{transform:translateX(6px);filter:hue-rotate(30deg)}}@keyframes wait{to{opacity:.5}}@keyframes steam{to{transform:translate(-50%,-20px);opacity:.2}}@media(prefers-reduced-motion:reduce){.pic,.effect{animation:none!important}}
    </style><div class="wrap"><div class="top"><b>🍳 요리 실험대</b><span id="counter"></span></div><div id="badge" class="badge"></div><div class="bench" id="bench"></div><div id="caption"></div><input id="seek" type="range" min="0" value="0" aria-label="단계 선택"><div class="controls"><button id="prev">이전</button><button id="play">▶ 재생</button><button id="next">다음</button><button id="reset">처음</button></div><p class="note">선택한 재료·기구·동작을 표현하는 도식 애니메이션입니다. 단계당 3초이며 실제 조리 시간과 다릅니다.</p></div><script>
    const data=__DATA__,$=id=>document.getElementById(id);let pos=0,timer=null,paused=false;
    const modes={'자르기':'chop','다지기':'chop','넣기':'move','섞기':'mix','볶기':'heat','굽기':'heat','끓이기':'heat','삶기':'heat','찌기':'heat','튀기기':'heat','데우기':'heat','뒤집기':'flip','붓기':'pour','담기':'move','식히기':'cool','기다리기':'wait','꺼내기':'move','무치기':'mix'};
    const effects={'자르기':'🔪','다지기':'🔪','넣기':'⬇️','섞기':'🥄','볶기':'🔥','굽기':'🔥','끓이기':'♨️','삶기':'♨️','찌기':'♨️','튀기기':'🫧','데우기':'♨️','뒤집기':'↪️','붓기':'💧','담기':'🍽️','식히기':'❄️','기다리기':'⏳','꺼내기':'⬆️','무치기':'🥄'};
    function span(cls,text){let e=document.createElement('span');e.className=cls;e.textContent=text;return e}
    function stop(){clearInterval(timer);timer=null;$('play').textContent='▶ 재생'}
    function pause(value){paused=value;document.querySelectorAll('.pic,.effect').forEach(e=>e.style.animationPlayState=value?'paused':'running')}
    function render(){const current=pos?data.steps[pos-1]:null;const bench=$('bench');bench.replaceChildren();
    data.tools.forEach((t,i)=>{let el=document.createElement('div');el.className='vessel'+(current&&current.tools.includes(t.name)?' chosen':'');el.style.left=((i+1)*100/(data.tools.length+1))+'%';el.append(span('pic',t.icon),span('name',t.name));bench.append(el)});
    data.foods.forEach((f,i)=>{let history=data.steps.slice(0,pos).filter(s=>s.ingredients.includes(f.name));let last=history.at(-1);let active=current&&current.ingredients.includes(f.name);let target=last&&last.tools.length?data.tools.findIndex(t=>t.name===last.tools[last.tools.length-1]):-1;let el=document.createElement('div');el.className='food'+(active?' '+(modes[current.action]||''):'');let left=target>=0?(target+1)*100/(data.tools.length+1):(i%8+.5)*100/8;el.style.left='calc('+left+'% - 35px + '+(target>=0?(i%3-1)*20:0)+'px)';el.style.top=(target>=0?120-Math.floor(i/3)*23:20+Math.floor(i/8)*76)+'px';let cut=history.some(s=>['자르기','다지기'].includes(s.action));el.append(span('pic',cut?f.icon+'▪':f.icon),span('name',f.name),span('status',last?last.action+(active?' 중':' 완료'):'준비'));bench.append(el)});
    if(current&&effects[current.action]){let e=document.createElement('div');e.className='effect '+(modes[current.action]==='chop'?'knife':'steam');e.textContent=effects[current.action];let ti=data.tools.findIndex(t=>t.name===current.tools.at(-1));if(ti>=0)e.style.left=((ti+1)*100/(data.tools.length+1))+'%';bench.append(e)}
    $('badge').textContent=current?current.action+' · '+current.ingredients.join(', ')+' → '+current.tools.join(', '):'재료와 기구가 준비되었습니다';$('caption').textContent=current?current.text+(current.seconds?' · 설정 조리 시간 '+current.seconds+'초':''):'';$('counter').textContent=pos+' / '+data.steps.length+' 단계';$('seek').max=data.steps.length;$('seek').value=pos;$('prev').disabled=!pos;$('next').disabled=pos===data.steps.length;$('play').disabled=!data.steps.length;pause(paused);}
    $('play').onclick=()=>{if(timer){stop();pause(true);return}if(pos===data.steps.length)pos=0;if(pos===0)pos=1;paused=false;render();$('play').textContent='Ⅱ 일시정지';timer=setInterval(()=>{if(pos<data.steps.length){pos++;render()}else{stop();pause(true)}},3000)};
    $('next').onclick=()=>{stop();paused=false;pos=Math.min(pos+1,data.steps.length);render()};$('prev').onclick=()=>{stop();paused=false;pos=Math.max(0,pos-1);render()};$('reset').onclick=()=>{stop();paused=false;pos=0;render()};$('seek').oninput=e=>{stop();paused=false;pos=Number(e.target.value);render()};render();
    </script></html>'''.replace("__DATA__", payload)
    components.html(document, height=570, scrolling=True)


def persist(item, event):
    updated = copy.deepcopy(item)
    updated["updated_at"] = now()
    updated["revision"] = updated.get("revision", 0) + 1
    updated.setdefault("history", []).append({"at":updated["updated_at"], "event":event})
    store.save(updated)
    st.session_state.current = updated
    return updated


st.markdown('''<style>
[data-testid="stAppViewContainer"]{background:#f7f9f7}.block-container{max-width:1200px;padding-top:2rem}h1,h2,h3{color:#243c2e}div.stButton>button[kind="primary"],div.stFormSubmitButton>button[kind="primary"]{background:#236447;border-color:#236447}div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:16px}.brand{font-size:30px;font-weight:850;letter-spacing:-1px;color:#204b36}.tag{font-size:14px;color:#65776c;margin:6px 0 20px}.cover{padding:24px 18px;background:linear-gradient(125deg,#dcecdf,#f2ead6);border-radius:12px;min-height:106px;font-size:30px}.meta{font-size:12px;color:#64766a;margin:10px 0;overflow-wrap:anywhere}
</style>''', unsafe_allow_html=True)
st.markdown('<div class="brand">🍳 레시피룸</div><div class="tag">재료로 시작해, 나만의 레시피로 남기다.</div>', unsafe_allow_html=True)
try:
    store = Store()
except Exception as e:
    st.error(str(e)); st.stop()
if store.remote:
    st.caption("☁️ 영구 저장 연결 · 이 앱의 접속자들이 같은 보관함을 사용합니다. 개인용 접근 제한을 권장합니다.")
else:
    st.warning("임시 로컬 저장 모드입니다. Cloud 재시작·재배포 시 기록이 사라질 수 있습니다. 영구 보관하려면 app.py 상단 안내대로 비공개 데이터 저장소를 연결하세요.")
st.caption("각 단계의 재료·기구·동작을 직접 선택하세요. 설명 문장은 해석하지 않습니다.")
if "current" not in st.session_state: st.session_state.current = None
if "editing" not in st.session_state: st.session_state.editing = False
if "prepared" not in st.session_state: st.session_state.prepared = None
if "pending" not in st.session_state: st.session_state.pending = None
if "preview" not in st.session_state: st.session_state.preview = None
if "page" not in st.session_state: st.session_state.page = "새 레시피"
page = st.radio("메뉴", ["새 레시피", "내 보관함"], horizontal=True, key="page")

if page == "내 보관함":
    st.subheader("내 레시피 보관함")
    st.caption("카드를 열면 조리 과정을 다시 재생하고, 제목을 바꾸거나 수정·삭제할 수 있습니다.")
    try:
        records = store.all()
    except Exception as e:
        st.error(f"보관함을 읽지 못했습니다: {e}"); records = []
    if not records: st.info("저장한 레시피가 아직 없습니다. 새 레시피에서 첫 시뮬레이션을 만들어 보세요.")
    for start in range(0, len(records), 3):
        for col, item in zip(st.columns(3), records[start:start+3]):
            with col, st.container(border=True):
                cover = " ".join(icon(x) for x in item["ingredients"][:4])
                st.markdown(f'<div class="cover">{html.escape(cover)}</div>', unsafe_allow_html=True)
                st.subheader(item["title"])
                st.caption(f'{item["updated_at"][:16].replace("T", " ")} · {len(item["steps"])}단계')
                st.write(", ".join(item["ingredients"][:5]))
                if st.button("레시피 열기 →", key="open_"+item["id"], use_container_width=True):
                    st.session_state.current = copy.deepcopy(item)
                    st.session_state.editing = False
                    st.session_state.prepared = None
                    st.session_state.pending = None
                    st.session_state.preview = None
                    st.session_state.page = "새 레시피"
                    st.rerun()
else:
    item = st.session_state.current
    if item and not st.session_state.editing:
        # Title is editable only after a completed, saved simulation.
        title_key = "title_"+item["id"]+"_"+str(item.get("revision", 0))
        def rename():
            title = st.session_state[title_key].strip() or "이름 없는 요리"
            try:
                changed = copy.deepcopy(st.session_state.current); changed["title"] = title
                persist(changed, "제목 변경")
            except Exception as e:
                st.session_state.save_error = str(e)
        st.text_input("레시피 제목 · 최대 10자", value=item["title"], max_chars=10, key=title_key, on_change=rename)
        if st.session_state.get("save_error"):
            st.error(st.session_state.pop("save_error"))
        st.success("자동 저장된 레시피입니다. 아래 재생 버튼으로 언제든 다시 볼 수 있습니다.")
        scene(item["ingredients"], item["tools"], item["steps"])
        with st.expander("조리 방법 · 실행 기록", expanded=False):
            st.text(item["instructions"])
            st.caption(f'수정 버전 {item.get("revision", 1)}')
            for entry in reversed(item.get("history", [])):
                st.write(f'{entry["at"][:19].replace("T", " ")} · {entry["event"]}')
            st.caption("실행 기록은 시뮬레이션 생성·수정·제목 변경을 기록합니다. 단순 영상 재생은 새 실행으로 기록하지 않습니다.")
        c1,c2,c3 = st.columns(3)
        if c1.button("✏️ 시뮬레이션 수정", use_container_width=True):
            st.session_state.editing = True; st.session_state.prepared = None; st.session_state.pop("editor_steps", None); st.rerun()
        if c2.button("＋ 새 레시피", use_container_width=True):
            st.session_state.current = None; st.session_state.prepared = None; st.session_state.preview = None; st.session_state.pop("editor_steps", None); st.rerun()
        if c3.button("🗑️ 삭제", use_container_width=True):
            st.session_state.confirm_delete = item["id"]
        if st.session_state.get("confirm_delete") == item["id"]:
            st.warning("이 레시피를 보관함에서 삭제할까요? 앱에서는 되돌릴 수 없습니다.")
            yes,no = st.columns(2)
            if yes.button("삭제 확정", type="primary"):
                try:
                    store.delete(item); st.session_state.current = None
                    st.session_state.confirm_delete = None; st.session_state.prepared = None
                    st.rerun()
                except Exception as e: st.error(str(e))
            if no.button("취소"):
                st.session_state.confirm_delete = None; st.rerun()
    else:
        st.subheader("레시피 수정" if item else "새로운 요리를 시작해 볼까요?")
        if item and st.button("수정 취소 · 저장본으로 돌아가기"):
            st.session_state.editing = False; st.session_state.prepared = None
            st.session_state.preview = None; st.session_state.pending = None; st.rerun()
        draft_id = item["id"] if item else "new"
        with st.form("supplies_"+draft_id):
            a,b = st.columns(2)
            food_text = a.text_area("① 음식 재료", value=", ".join(item["ingredients"]) if item else "", placeholder="양파, 계란, 식용유, 소금", max_chars=1000, height=100)
            tool_text = b.text_area("② 조리 기구", value=", ".join(item["tools"]) if item else "", placeholder="칼, 도마, 프라이팬, 접시", max_chars=1000, height=100)
            ready = st.form_submit_button("재료·기구를 조리대에 올리기", type="primary")
        if ready:
            try:
                foods, tools = names(food_text), names(tool_text)
                if not foods or not tools: raise ValueError("재료와 기구를 각각 한 개 이상 입력해 주세요.")
                st.session_state.prepared = {"ingredients":foods, "tools":tools}
                st.session_state.preview = None; st.session_state.pending = None
            except ValueError as e: st.error(str(e))
        prepared = st.session_state.prepared
        if prepared:
            st.caption("현재 조리대 · " + ", ".join(prepared["ingredients"]) + " / " + ", ".join(prepared["tools"]))
            scene(prepared["ingredients"], prepared["tools"])
            st.caption("위 입력칸을 바꿨다면 ‘조리대에 올리기’를 다시 눌러 적용하세요.")
            if "editor_steps" not in st.session_state:
                st.session_state.editor_steps = [dict(step, uid=uuid.uuid4().hex) for step in (item.get("steps", []) if item else [])]
            plan = st.session_state.editor_steps
            st.subheader("③ 조리 단계 만들기")
            st.caption("그림은 선택한 항목을 따릅니다. 설명만 바꾸면 동작은 바뀌지 않습니다. 기구를 여러 개 고르면 재료는 마지막으로 선택한 기구 위치로 이동합니다.")
            if item and any(x.get("action") not in ACTIONS for x in plan):
                st.info("이전 버전의 글 전용 단계에는 동작이 없습니다. 재료·기구·동작을 선택한 뒤 다시 저장해 주세요.")
            if st.button("＋ 단계 추가", disabled=len(plan)>=30):
                plan.append({"uid":uuid.uuid4().hex,"action":"자르기","ingredients":[],"tools":[],"seconds":0,"text":""})
                st.rerun()
            for i, step in enumerate(plan):
                uid=step["uid"]
                with st.container(border=True):
                    st.markdown(f"**{i+1}단계**")
                    a,b,c=st.columns(3)
                    step["ingredients"]=a.multiselect("대상 재료", prepared["ingredients"], default=[n for n in step["ingredients"] if n in prepared["ingredients"]], key="food_"+uid)
                    step["tools"]=b.multiselect("사용 기구", prepared["tools"], default=[n for n in step["tools"] if n in prepared["tools"]], key="tool_"+uid)
                    options=list(ACTIONS)
                    step["action"]=c.selectbox("조리 동작", options, index=options.index(step["action"]) if step["action"] in options else 0, key="act_"+uid)
                    step["text"]=st.text_input("설명 · 선택 사항", value=step["text"], max_chars=300, key="desc_"+uid)
                    step["seconds"]=st.number_input("조리 시간(초) · 기록용", min_value=0, max_value=86400, value=int(step.get("seconds",0)), key="time_"+uid)
                    up,down,delete=st.columns(3)
                    if up.button("↑ 위로", key="up_"+uid, disabled=i==0):
                        plan[i-1],plan[i]=plan[i],plan[i-1];st.rerun()
                    if down.button("↓ 아래로", key="down_"+uid, disabled=i==len(plan)-1):
                        plan[i+1],plan[i]=plan[i],plan[i+1];st.rerun()
                    if delete.button("단계 삭제", key="del_"+uid):
                        plan.pop(i);st.rerun()
            if st.button("선택한 단계로 미리보기", disabled=not plan):
                try:
                    st.session_state.preview=validate_steps(plan,prepared["ingredients"],prepared["tools"])
                except ValueError as e: st.error(str(e))
            if st.session_state.preview:
                st.caption("마지막으로 ‘미리보기’를 눌렀을 때의 단계입니다. 변경 후에는 다시 눌러 주세요.")
                scene(prepared["ingredients"],prepared["tools"],st.session_state.preview)
            if st.button("시뮬레이션 완성 · 자동 저장", type="primary", disabled=not plan):
                st.session_state.pending=None
                try:
                    selected=validate_steps(plan,prepared["ingredients"],prepared["tools"])
                    for step in selected:
                        step.pop("uid",None)
                        if not step["text"].strip():
                            step["text"] = ", ".join(step["ingredients"])+" · "+step["action"]+" · "+", ".join(step["tools"])
                    result=copy.deepcopy(item) if item else {"id":uuid.uuid4().hex,"title":"이름 없는 요리","created_at":now(),"history":[],"revision":0}
                    result.update({"ingredients":prepared["ingredients"][:],"tools":prepared["tools"][:],"steps":selected,"instructions":"\n".join(x["text"] for x in selected),"engine":"단계 직접 선택"})
                    st.session_state.pending=result
                except ValueError as e: st.error(str(e))
            if st.session_state.pending:
                try:
                    with st.spinner("시뮬레이션을 저장하고 있습니다…"):
                        persist(st.session_state.pending, "시뮬레이션 수정 실행" if item else "시뮬레이션 최초 실행")
                    st.session_state.pending = None; st.session_state.preview = None
                    st.session_state.editing = False; st.session_state.prepared = None; st.rerun()
                except Exception as e:
                    st.error(f"자동 저장 실패 — 입력한 내용은 유지됩니다. {e}")
                    if st.button("자동 저장 다시 시도"):
                        st.rerun()
st.divider()
st.caption("🍳 레시피룸 · 요리의 순서를 기록하는 나만의 실험 노트 | 실제 조리 시 식품별 충분한 가열과 알레르기·위생 기준을 따르세요.")
