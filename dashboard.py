import json
import os
import re
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT     = Path(__file__).parent
CUSTOMERS_DIR = REPO_ROOT / "customers"


def _secret(key: str, default=None):
    """Streamlit Secrets 우선, 없으면 환경변수"""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


# ─── Git 자동 push ────────────────────────────────────────────

def git_commit_and_push(commit_message: str) -> tuple[bool, str]:
    """변경된 customers/*.json을 commit & push. (ok, message)"""
    token = _secret("GITHUB_TOKEN")
    repo  = _secret("GITHUB_REPO")  # 예: "minsung6333/nara-monitor"
    name  = _secret("GIT_USER_NAME",  "Nara Dashboard Bot")
    email = _secret("GIT_USER_EMAIL", "bot@clabi.ai")

    if not token or not repo:
        return False, "GITHUB_TOKEN 또는 GITHUB_REPO 미설정 (로컬 저장만 됨)"

    try:
        # 변경사항 확인
        status = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "customers/"],
            capture_output=True, text=True, encoding="utf-8",
        )
        if not status.stdout.strip():
            return True, "변경사항 없음 (이미 최신)"

        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"]     = name
        env["GIT_AUTHOR_EMAIL"]    = email
        env["GIT_COMMITTER_NAME"]  = name
        env["GIT_COMMITTER_EMAIL"] = email

        # add + commit
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "add", "customers/"],
            check=True, env=env,
        )
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "commit", "-m", commit_message],
            check=True, env=env, capture_output=True,
        )

        # token push
        remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"
        push = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "push", remote_url, "HEAD:main"],
            capture_output=True, text=True, env=env,
        )
        if push.returncode != 0:
            return False, f"git push 실패: {push.stderr[:200]}"

        return True, "GitHub에 push 완료"
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="ignore")[:200] if isinstance(e.stderr, bytes) else str(e)
        return False, f"git 명령 실패: {err}"
    except Exception as e:
        return False, f"오류: {e}"


def trigger_github_action() -> tuple[bool, str]:
    """GitHub Actions 'daily.yml' workflow 즉시 실행 트리거"""
    token = _secret("GITHUB_TOKEN")
    repo  = _secret("GITHUB_REPO")

    if not token or not repo:
        return False, "GITHUB_TOKEN 또는 GITHUB_REPO 미설정"

    url = f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/dispatches"
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": "main"},
            timeout=15,
        )
        if r.status_code == 204:
            return True, "GitHub Actions 실행됨 — 5분 내 메일 도착 예정"
        return False, f"트리거 실패 ({r.status_code}): {r.text[:200]}"
    except Exception as e:
        return False, f"오류: {e}"

# ─── 유틸 ─────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_]+", "-", text)
    return text or "customer"


def short_name(name: str) -> str:
    return name.replace("주식회사 ", "").replace(" Co., Ltd.", "").split("(")[0].strip()


def load_all() -> dict:
    result = {}
    for d in sorted(CUSTOMERS_DIR.iterdir()):
        if not d.is_dir():
            continue
        cfg_path = d / "config.json"
        if not cfg_path.exists():
            continue
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        profile = {}
        if (d / "profile.json").exists():
            profile = json.loads((d / "profile.json").read_text(encoding="utf-8"))
        result[d.name] = {"config": cfg, "profile": profile}
    return result


def save_config(customer_id: str, config: dict):
    path = CUSTOMERS_DIR / customer_id / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def save_profile(customer_id: str, profile: dict):
    path = CUSTOMERS_DIR / customer_id / "profile.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def blank_profile(company_name: str) -> dict:
    return {
        "company_name": company_name,
        "description": "",
        "business_areas": [],
        "core_technologies": [],
        "certifications": [],
        "target_sectors": [],
        "references": [],
        "exclusions": [],
        "min_amount": 0,
        "notes": "",
    }


# ─── 기관명 API 검색 ──────────────────────────────────────────

def _fetch_filtered(url: str, params: dict) -> tuple[list, int]:
    """서버 필터 적용, 1페이지(100건) 호출. (items, totalCount) 반환"""
    try:
        r = requests.get(
            url,
            params={**params, "pageNo": "1", "numOfRows": "100"},
            timeout=10,
        )
        r.raise_for_status()
        body  = r.json()["response"]["body"]
        total = int(body.get("totalCount", 0))
        items = body.get("items") or []
        return ([items] if isinstance(items, dict) else items), total
    except Exception:
        return [], 0


def search_agency_preview(agency_name: str, days: int = 30) -> dict:
    """
    나라장터 기관명 서버 필터링 검색. API 3회 호출.
    검증된 파라미터:
      - 입찰공고: ntceInsttNm (공고기관명)
      - 사전규격: dminsttNm  (수요기관명)
      - 발주계획: dminsttNm
    """
    from config import SERVICE_KEY, ENDPOINTS

    today      = datetime.now()
    date_start = (today - timedelta(days=days)).strftime("%Y%m%d") + "0000"
    date_end   = today.strftime("%Y%m%d") + "2359"

    base = {
        "ServiceKey": SERVICE_KEY,
        "type":       "json",
        "inqryDiv":   "1",
        "inqryBgnDt": date_start,
        "inqryEndDt": date_end,
    }

    kw      = agency_name.lower()
    results = []
    totals  = {"입찰공고": 0, "사전규격": 0, "발주계획": 0}

    def _matches(item: dict, *fields: str) -> bool:
        return any(kw in (item.get(f) or "").lower() for f in fields)

    # ① 입찰공고 (용역 + 물품) — ntceInsttNm 서버 필터
    for ep_key in ("bid_servc", "bid_thng"):
        items, total = _fetch_filtered(ENDPOINTS[ep_key], {**base, "ntceInsttNm": agency_name})
        totals["입찰공고"] += total
        for item in items:
            if not _matches(item, "ntceInsttNm", "dminsttNm"):
                continue
            results.append({
                "구분":    "입찰공고",
                "공고명":  item.get("bidNtceNm", ""),
                "공고기관": item.get("ntceInsttNm", ""),
                "수요기관": item.get("dminsttNm", ""),
                "등록일":  (item.get("bidNtceDt") or "")[:10],
            })

    # ② 사전규격 (용역 + 물품) — dminsttNm 서버 필터
    for ep_key in ("spec_servc", "spec_thng"):
        items, total = _fetch_filtered(ENDPOINTS[ep_key], {**base, "dminsttNm": agency_name})
        totals["사전규격"] += total
        for item in items:
            if not _matches(item, "orderInsttNm", "rlDminsttNm"):
                continue
            results.append({
                "구분":    "사전규격",
                "공고명":  item.get("prdctClsfcNoNm", ""),
                "공고기관": item.get("orderInsttNm", ""),
                "수요기관": item.get("rlDminsttNm", ""),
                "등록일":  (item.get("rcptDt") or "")[:10],
            })

    # ③ 발주계획 (용역 + 물품) — orderInsttNm 서버 필터 (검증 완료)
    for ep_key in ("plan_servc", "plan_thng"):
        items, total = _fetch_filtered(ENDPOINTS[ep_key], {**base, "orderInsttNm": agency_name})
        totals["발주계획"] += total
        for item in items:
            if not _matches(item, "orderInsttNm", "totlmngInsttNm"):
                continue
            results.append({
                "구분":    "발주계획",
                "공고명":  item.get("bizNm", ""),
                "공고기관": item.get("orderInsttNm", ""),
                "수요기관": item.get("totlmngInsttNm", ""),
                "등록일":  (item.get("nticeDt") or "")[:10],
            })

    results.sort(key=lambda x: x["등록일"], reverse=True)

    more = {k: v for k, v in totals.items() if v > 100}
    return {"results": results, "totals": totals, "more": more}


# ─── PDF → profile 자동 생성 ──────────────────────────────────

PROFILE_SYSTEM = """당신은 공공조달 입찰 전략 전문가입니다.
주어진 회사 소개서 텍스트를 읽고, 이 회사가 공공입찰 공고의 적합성을 판단하는 데 가장 유용한 프로필 JSON을 생성하세요.

## 필수 기본 필드 (항상 포함)
{
  "company_name": "회사 정식명칭",
  "description": "한두 줄 핵심 사업 설명",
  "business_areas": ["이 회사가 수행 가능한 사업 영역"],
  "core_technologies": ["보유 핵심 기술·솔루션"],
  "certifications": ["보유 인증·자격·파트너십"],
  "target_sectors": ["주요 고객 섹터 (중앙부처, 지자체, 공기업 등)"],
  "references": ["대표 수행 실적 (기관명 + 사업명)"],
  "exclusions": ["수주 불가·관심 없는 영역 (경쟁력 없거나 사업 방향과 무관한 것)"],
  "notes": "수주 전략상 특이사항 (인증 활용법, 파트너십, 수의계약 가능 여부 등)"
}

## 회사 유형별 추가 필드 (해당하는 경우에만 생성)
소개서 내용을 분석하여 이 회사를 평가할 때 중요한 추가 카테고리가 있다면
snake_case 영문 key로 자유롭게 추가하고, 반드시 "_labels"에 한국어 표시명을 함께 등록하세요.

예시:
{
  "sw_grade": ["고급 1등급"],
  "csap_status": ["SaaS 표준등급 인증 완료"],
  "_labels": {
    "sw_grade": "SW사업자 등급",
    "csap_status": "클라우드 보안인증 현황"
  }
}

회사 유형별 추가 필드 예시:
- IT·SW 기업: sw_grade(SW사업자 등급), csap_status(클라우드 보안인증)
- 건설·엔지니어링: construction_license(면허 공종), construction_records(시공 실적)
- 컨설팅: consulting_areas(전문 컨설팅 분야), methodology(주요 방법론)
- 연구기관: research_fields(연구 분야), lab_equipment(보유 장비)

## 규칙
- 없는 항목은 빈 배열 [] 또는 빈 문자열로
- 추가 필드의 value는 반드시 문자열 배열 형태로
- 추가 필드는 반드시 "_labels"에 한국어 표시명 매핑 등록
- 기본 필드(business_areas 등)는 _labels에 등록 불필요
- 반드시 JSON만 반환 (설명 텍스트 없이)"""


def extract_profile_from_pdf(pdf_bytes: bytes) -> dict:
    try:
        import fitz
    except ImportError:
        st.error("PyMuPDF 미설치")
        return {}
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        st.error("OPENAI_API_KEY 환경변수가 없습니다.")
        return {}

    client = OpenAI(api_key=api_key)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        doc = fitz.open(tmp_path)
        pages = [doc[i].get_text() for i in range(min(len(doc), 30))]
        full_text = "\n\n".join(pages)[:60000]
        doc.close()
    finally:
        os.unlink(tmp_path)

    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": PROFILE_SYSTEM},
            {"role": "user",   "content": full_text},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        timeout=120,
    )
    return json.loads(resp.choices[0].message.content or "{}")


# ─── 리스트 편집 컴포넌트 ─────────────────────────────────────

def list_editor(state_key: str, field: str, placeholder: str = ""):
    items: list = st.session_state[state_key].get(field, [])
    if items:
        cols = st.columns(min(len(items), 3))
        for i, item in enumerate(list(items)):
            with cols[i % len(cols)]:
                if st.button(item[:28], key=f"del_{state_key}_{field}_{i}",
                             use_container_width=True, help=f"클릭하여 삭제: {item}"):
                    st.session_state[state_key][field].remove(item)
                    st.rerun()
    else:
        st.caption("없음")

    with st.form(f"add_{state_key}_{field}", clear_on_submit=True):
        val = st.text_input("추가", placeholder=placeholder, label_visibility="collapsed")
        if st.form_submit_button("➕"):
            v = val.strip()
            if v and v not in st.session_state[state_key][field]:
                st.session_state[state_key][field].append(v)
                st.rerun()


# ─── 신규 고객 다이얼로그 ─────────────────────────────────────

@st.dialog("신규 고객사 추가", width="large")
def new_customer_dialog():
    st.caption("소개서 PDF를 업로드하면 GPT가 회사 프로필을 자동으로 생성합니다.")

    col1, col2 = st.columns(2)
    with col1:
        company_name = st.text_input("회사명 *", placeholder="주식회사 예시")
    with col2:
        customer_id = st.text_input(
            "고객 ID (폴더명)",
            value=slugify(company_name) if company_name else "",
            placeholder="example-corp",
        )

    col3, col4 = st.columns(2)
    with col3:
        kw_input = st.text_input("검색 키워드 (쉼표 구분)", placeholder="AI,인공지능,데이터")
    with col4:
        mail_to = st.text_input("수신 이메일 *", placeholder="contact@company.com")

    st.divider()
    pdf_file = st.file_uploader("회사 소개서 PDF (선택)", type=["pdf"])

    if st.button("등록", type="primary", use_container_width=True):
        if not company_name.strip():
            st.error("회사명을 입력하세요.")
            return
        if not mail_to.strip():
            st.error("수신 이메일을 입력하세요.")
            return

        cid = re.sub(r"[^\w-]", "", customer_id or slugify(company_name)).lower()
        dest = CUSTOMERS_DIR / cid

        if dest.exists():
            st.error(f"'{cid}' ID가 이미 존재합니다.")
            return

        keywords = [k.strip() for k in kw_input.split(",") if k.strip()] or ["AI", "인공지능"]

        if pdf_file:
            with st.spinner("PDF 분석 중..."):
                profile = extract_profile_from_pdf(pdf_file.read())
            if profile:
                profile.setdefault("company_name", company_name)
            else:
                profile = blank_profile(company_name)
        else:
            profile = blank_profile(company_name)

        config = {
            "company_name": company_name.strip(),
            "keywords": keywords,
            "mail_to": mail_to.strip(),
            "active": True,
            "tracked_agencies": [],
        }
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        (dest / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

        st.success(f"✅ '{company_name}' 등록 완료!")
        st.session_state["view"] = "home"
        st.rerun()


# ─── Page setup ───────────────────────────────────────────────

st.set_page_config(page_title="나라장터 AI 관리", page_icon="🏛️", layout="wide")


# ─── 비밀번호 인증 게이트 ─────────────────────────────────────

def _get_dashboard_password() -> str | None:
    """Streamlit Secrets 또는 환경변수에서 비밀번호 조회."""
    return _secret("DASHBOARD_PASSWORD")


def check_auth():
    """비밀번호 입력 게이트. 통과 전까지 페이지 렌더링 차단."""
    if st.session_state.get("authenticated"):
        return

    password = _get_dashboard_password()
    if not password:
        st.error(
            "🔒 **DASHBOARD_PASSWORD**가 설정되지 않았습니다.\n\n"
            "로컬: `.env` 파일에 `DASHBOARD_PASSWORD=...` 추가  \n"
            "Streamlit Cloud: 앱 Settings → Secrets에 등록"
        )
        st.stop()

    # 로그인 화면
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        st.markdown("<div style='height: 60px'></div>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("### 🏛️ 나라장터 AI 관리 대시보드")
            st.caption("접근 권한이 있는 사용자만 로그인 가능합니다.")
            st.write("")

            with st.form("login_form"):
                pw_input = st.text_input(
                    "비밀번호",
                    type="password",
                    placeholder="비밀번호를 입력하세요",
                )
                submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
                if submitted:
                    if pw_input == password:
                        st.session_state["authenticated"] = True
                        st.rerun()
                    else:
                        st.error("비밀번호가 틀렸습니다.")
    st.stop()


check_auth()

# ─── 인증 통과 후 ─────────────────────────────────────────────

if "view"    not in st.session_state:
    st.session_state["view"]    = "home"
if "editing" not in st.session_state:
    st.session_state["editing"] = None

customers = load_all()

# ─── Sidebar (미니멀) ─────────────────────────────────────────

with st.sidebar:
    st.title("🏛️ 나라장터 AI")
    st.caption("공고 모니터링 관리 대시보드")
    st.divider()

    if st.button("🏠 고객사 목록", use_container_width=True,
                 disabled=(st.session_state["view"] == "home")):
        st.session_state["view"] = "home"
        st.rerun()

    if st.button("➕ 신규 고객사 추가", use_container_width=True):
        new_customer_dialog()

    st.divider()
    total   = len(customers)
    active  = sum(1 for d in customers.values() if d["config"].get("active", True))
    st.metric("전체 고객사", total)
    st.metric("활성화", active)

    st.divider()
    if st.button("🔒 로그아웃", use_container_width=True):
        st.session_state.clear()
        st.rerun()


# ════════════════════════════════════════════════════════
# 홈 — 고객사 카드 그리드
# ════════════════════════════════════════════════════════

def show_home():
    st.header("고객사 관리")
    st.caption(f"총 {len(customers)}개 고객사")
    st.divider()

    cols = st.columns(3)
    for i, (cid, data) in enumerate(customers.items()):
        cfg    = data["config"]
        active = cfg.get("active", True)
        name   = short_name(cfg.get("company_name", cid))
        kws    = cfg.get("keywords", [])
        ags    = cfg.get("tracked_agencies", [])

        with cols[i % 3]:
            with st.container(border=True):
                badge = "🟢 활성" if active else "🔴 비활성"
                st.markdown(f"**{name}**")
                st.caption(f"{badge}  ·  ID: `{cid}`")

                st.caption(f"📧 {cfg.get('mail_to', '-')}")

                if kws:
                    kw_str = "  ".join(f"`{k}`" for k in kws[:5])
                    kw_more = f" +{len(kws)-5}" if len(kws) > 5 else ""
                    st.markdown(f"🔑 {kw_str}{kw_more}")
                else:
                    st.caption("🔑 키워드 없음")

                if ags:
                    st.caption(f"🏢 트래킹 기관 {len(ags)}개")
                else:
                    st.caption("🏢 트래킹 기관 없음")

                st.write("")
                if st.button("편집", key=f"btn_edit_{cid}", use_container_width=True,
                             type="primary"):
                    st.session_state["view"]    = "edit"
                    st.session_state["editing"] = cid
                    st.rerun()


# ════════════════════════════════════════════════════════
# 편집 페이지
# ════════════════════════════════════════════════════════

def show_edit(cid: str):
    cfg     = customers[cid]["config"]
    profile = customers[cid]["profile"]

    kw_key   = f"kw_{cid}"
    ag_key   = f"ag_{cid}"
    prof_key = f"prof_{cid}"

    if kw_key   not in st.session_state:
        st.session_state[kw_key]   = list(cfg.get("keywords", []))
    if ag_key   not in st.session_state:
        st.session_state[ag_key]   = list(cfg.get("tracked_agencies", []))
    if prof_key not in st.session_state:
        st.session_state[prof_key] = json.loads(
            json.dumps(profile if profile else blank_profile(cfg.get("company_name", "")))
        )

    # 브레드크럼 + 회사명 편집
    col_back, col_name, col_id = st.columns([1, 4, 2])
    with col_back:
        if st.button("← 목록"):
            st.session_state["view"] = "home"
            st.rerun()
    with col_name:
        edited_company_name = st.text_input(
            "회사명",
            value=cfg.get("company_name", cid),
            key=f"company_name_{cid}",
            label_visibility="collapsed",
        )
    with col_id:
        st.caption(f"ID: `{cid}`")

    col_toggle, col_warn = st.columns([1, 4])
    with col_toggle:
        active_val = st.toggle("서비스 활성화", value=cfg.get("active", True),
                               key=f"active_{cid}")
    with col_warn:
        if not active_val:
            st.warning("비활성화 — 매일 자동 실행에서 제외됩니다.")

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["🔑 키워드", "📧 이메일", "🏢 기관 트래킹", "📋 회사 프로필"])

    # ── 키워드 ──
    with tab1:
        st.markdown("**검색 키워드** — 공고 제목·품명에 포함된 경우만 수집")
        keywords: list = st.session_state[kw_key]

        if keywords:
            st.caption("버튼 클릭으로 삭제")
            cols = st.columns(min(len(keywords), 5))
            for i, kw in enumerate(list(keywords)):
                with cols[i % len(cols)]:
                    if st.button(kw, key=f"del_kw_{i}", use_container_width=True):
                        st.session_state[kw_key].remove(kw)
                        st.rerun()
        else:
            st.info("키워드 없음")

        st.divider()
        with st.form("kw_form", clear_on_submit=True):
            new_kw = st.text_input("새 키워드", placeholder="예: 머신러닝")
            if st.form_submit_button("➕ 추가"):
                val = new_kw.strip()
                if val and val not in st.session_state[kw_key]:
                    st.session_state[kw_key].append(val)
                    st.rerun()
                elif val in st.session_state[kw_key]:
                    st.warning(f"'{val}'은 이미 있습니다.")

    # ── 이메일 ──
    with tab2:
        st.markdown("**수신 이메일 설정**")
        st.text_input("수신 주소", value=cfg.get("mail_to", ""),
                      key=f"mail_{cid}", placeholder="example@company.com")
        st.caption("변경 후 아래 **저장** 버튼을 눌러야 반영됩니다.")
        st.divider()
        st.info("**발신**: hello@clabi.ai  \n**SMTP**: smtp.worksmobile.com : 465 (NaverWorks SSL)")

    # ── 기관 트래킹 ──
    with tab3:
        st.markdown("**트래킹 중인 기관**")
        st.caption("키워드와 무관하게 이 기관의 **모든 공고**를 수집하여 AI가 분석합니다.")

        agencies: list = st.session_state[ag_key]

        if agencies:
            st.caption("버튼 클릭으로 삭제")
            cols = st.columns(min(len(agencies), 3))
            for i, ag in enumerate(list(agencies)):
                with cols[i % len(cols)]:
                    if st.button(ag, key=f"del_ag_{i}", use_container_width=True):
                        st.session_state[ag_key].remove(ag)
                        st.rerun()
        else:
            st.info("트래킹 중인 기관 없음")

        st.divider()

        # 기관 검색 테스트
        st.markdown("**🔍 기관 검색 테스트**")
        st.caption("기관명을 입력하면 나라장터 API에서 실제 공고를 조회하여 정확한 기관명을 확인할 수 있습니다.")

        search_key = f"agency_search_{cid}"

        col_input, col_days, col_btn = st.columns([3, 1, 1])
        with col_input:
            search_term = st.text_input(
                "기관명", placeholder="예: NIA, 교육청, 한국지능정보",
                label_visibility="collapsed", key=f"ag_search_input_{cid}"
            )
        with col_days:
            search_days = st.selectbox(
                "조회 기간",
                options=[14, 30, 60, 90, 180],
                index=1,
                format_func=lambda d: f"최근 {d}일",
                key=f"ag_search_days_{cid}",
                label_visibility="collapsed",
            )
        with col_btn:
            do_search = st.button("검색", use_container_width=True, key=f"ag_search_btn_{cid}")

        if do_search and search_term.strip():
            with st.spinner(f"'{search_term}' 검색 중... (API 6회 호출)"):
                data = search_agency_preview(search_term.strip(), days=search_days)
            st.session_state[search_key] = {
                "term":    search_term.strip(),
                "days":    search_days,
                "results": data["results"],
                "totals":  data["totals"],
                "more":    data["more"],
            }

        # 검색 결과 표시
        if search_key in st.session_state:
            cached = st.session_state[search_key]
            term   = cached["term"]
            res    = cached["results"]

            if res:
                # 기관명 유니크 목록 추출
                unique_agencies = sorted(set(
                    r["공고기관"] for r in res if r["공고기관"]
                ) | set(
                    r["수요기관"] for r in res if r["수요기관"]
                ))

                days_label = cached.get("days", 30)
                totals     = cached.get("totals", {})
                more       = cached.get("more", {})

                badge = "  ·  ".join(
                    f"{k} {v}건" for k, v in totals.items() if v > 0
                ) or "0건"
                st.success(
                    f"**'{term}'** — {badge} (최근 {days_label}일, 서버 필터링)"
                )
                if more:
                    more_str = ", ".join(f"{k} {v}건" for k, v in more.items())
                    st.caption(f"⚠️ 표시는 최신 100건. 전체: {more_str} — 기관명을 더 구체적으로 입력하면 정확도 향상")

                st.markdown("**발견된 기관명** — 클릭하면 트래킹 목록에 추가")
                ag_cols = st.columns(min(len(unique_agencies), 4))
                for i, ag in enumerate(unique_agencies):
                    with ag_cols[i % len(ag_cols)]:
                        already = ag in st.session_state[ag_key]
                        if already:
                            st.button(f"✅ {ag[:20]}", key=f"found_ag_{i}",
                                      disabled=True, use_container_width=True)
                        else:
                            if st.button(f"➕ {ag[:20]}", key=f"found_ag_{i}",
                                         use_container_width=True, help=ag):
                                st.session_state[ag_key].append(ag)
                                st.rerun()

                st.divider()
                st.markdown("**공고 목록 미리보기**")
                st.dataframe(
                    res,
                    column_config={
                        "구분":   st.column_config.TextColumn(width="small"),
                        "공고명": st.column_config.TextColumn(width="large"),
                        "공고기관": st.column_config.TextColumn(width="medium"),
                        "수요기관": st.column_config.TextColumn(width="medium"),
                        "등록일": st.column_config.TextColumn(width="small"),
                    },
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                days_label = cached.get("days", 30)
                st.warning(f"**'{term}'** 포함 기관의 공고를 최근 {days_label}일 내에서 찾을 수 없습니다.")
                st.caption("조회 기간을 늘리거나 기관명을 더 짧게 입력해보세요. 예: '인하대' → '인하'")

    # ── 회사 프로필 ──
    with tab4:
        prof = st.session_state[prof_key]

        with st.expander("📄 소개서 PDF로 프로필 자동 생성", expanded=not bool(profile)):
            st.caption("PDF를 업로드하면 GPT가 분석하여 아래 필드를 자동으로 채웁니다.")
            pdf_file = st.file_uploader("소개서 PDF", type=["pdf"], key=f"pdf_{cid}")
            if pdf_file and st.button("🤖 프로필 생성", type="primary"):
                with st.spinner("PDF 분석 중... (30초~1분 소요)"):
                    try:
                        generated = extract_profile_from_pdf(pdf_file.read())
                        if generated:
                            generated.setdefault("min_amount", prof.get("min_amount", 0))
                            st.session_state[prof_key] = generated
                            st.success("프로필 자동 생성 완료! 아래에서 검토 후 저장하세요.")
                            st.rerun()
                        else:
                            st.error("프로필 생성 실패. API 키를 확인하세요.")
                    except Exception as e:
                        st.error(f"오류: {e}")

        st.divider()
        st.markdown("**프로필 직접 편집**")

        col_a, col_b = st.columns(2)
        with col_a:
            prof["company_name"] = st.text_input(
                "회사명", value=prof.get("company_name", ""), key=f"p_name_{cid}")
        with col_b:
            prof["min_amount"] = st.number_input(
                "최소 금액 기준 (원)", value=int(prof.get("min_amount") or 0),
                step=1_000_000, key=f"p_amt_{cid}")

        prof["description"] = st.text_area(
            "회사 설명", value=prof.get("description", ""),
            height=80, key=f"p_desc_{cid}")

        st.divider()

        # 고정 필드 레이블 매핑 (기본 필드)
        KNOWN_LABELS = {
            "business_areas":    "사업 영역",
            "core_technologies": "핵심 기술",
            "certifications":    "인증·파트너십",
            "target_sectors":    "주요 고객 섹터",
            "references":        "주요 레퍼런스",
            "exclusions":        "제외 영역",
        }
        FIXED_SKIP = {"company_name", "description", "min_amount", "notes"}

        # LLM이 생성한 커스텀 필드 레이블 (profile._labels)
        if "_labels" not in prof or not isinstance(prof.get("_labels"), dict):
            prof["_labels"] = {}
        custom_labels: dict = prof["_labels"]

        # 현재 profile에 있는 리스트 필드 수집
        list_field_keys = [
            k for k, v in prof.items()
            if isinstance(v, list) and k not in FIXED_SKIP and not k.startswith("_")
        ]

        col_l, col_r = st.columns(2)
        for idx, field in enumerate(list_field_keys):
            label = (
                KNOWN_LABELS.get(field)
                or custom_labels.get(field)
                or field.replace("_", " ")
            )
            is_custom = field not in KNOWN_LABELS

            with (col_l if idx % 2 == 0 else col_r):
                col_lbl, col_del = st.columns([5, 1])
                with col_lbl:
                    if is_custom:
                        # 커스텀 필드: 레이블 인라인 편집 가능
                        new_label = st.text_input(
                            "레이블",
                            value=label,
                            key=f"label_{cid}_{field}",
                            label_visibility="collapsed",
                        )
                        if new_label and new_label != label:
                            custom_labels[field] = new_label
                        st.caption(f"`{field}`")
                    else:
                        st.markdown(f"**{label}**")
                with col_del:
                    if is_custom:
                        if st.button("✕", key=f"del_section_{cid}_{field}",
                                     help=f"'{label}' 섹션 삭제"):
                            del prof[field]
                            custom_labels.pop(field, None)
                            st.rerun()
                list_editor(prof_key, field)
                st.write("")

        # 새 섹션 추가
        st.divider()
        with st.expander("➕ 새 섹션 추가"):
            st.caption("이 고객사 프로필에만 사용되는 커스텀 섹션을 추가합니다.")
            col_fn, col_lb = st.columns(2)
            with col_fn:
                new_field = st.text_input(
                    "필드 ID (영문 snake_case)",
                    placeholder="예: key_personnel",
                    key=f"new_section_field_{cid}",
                )
            with col_lb:
                new_label_input = st.text_input(
                    "한글 표시명",
                    placeholder="예: 핵심 인력",
                    key=f"new_section_label_{cid}",
                )
            if st.button("추가", key=f"add_section_{cid}"):
                fn = re.sub(r"[^\w]", "_", (new_field or "").strip().lower())
                lb = (new_label_input or "").strip()
                if not fn:
                    st.warning("필드 ID를 입력하세요.")
                elif fn in prof or fn in FIXED_SKIP:
                    st.warning("이미 존재하는 필드입니다.")
                else:
                    prof[fn] = []
                    if lb:
                        custom_labels[fn] = lb
                    st.rerun()

        st.divider()
        prof["notes"] = st.text_area(
            "특이사항", value=prof.get("notes", ""),
            placeholder="수주 전략상 특이사항",
            height=80, key=f"p_notes_{cid}")

    # ── 저장 / 취소 / 지금 실행 ──
    st.divider()
    col_save, col_run, col_reset, _ = st.columns([1.2, 1.2, 1, 4])

    with col_save:
        if st.button("💾 저장 + 배포", type="primary", use_container_width=True,
                     help="로컬 저장 + GitHub에 push (다음 06:50 자동 발송 시 반영)"):
            new_config = {
                "company_name":     st.session_state.get(f"company_name_{cid}", cfg.get("company_name", "")),
                "keywords":         st.session_state[kw_key],
                "mail_to":          st.session_state.get(f"mail_{cid}", cfg.get("mail_to", "")),
                "active":           st.session_state.get(f"active_{cid}", cfg.get("active", True)),
                "tracked_agencies": st.session_state[ag_key],
            }
            save_config(cid, new_config)
            save_profile(cid, st.session_state[prof_key])
            st.success("✅ 로컬 저장 완료")

            company = new_config["company_name"] or cid
            with st.spinner("GitHub에 push 중..."):
                ok, msg = git_commit_and_push(f"dashboard: update {cid} ({company})")
            if ok:
                st.success(f"☁️ {msg}")
            else:
                st.warning(f"⚠️ {msg}")

    with col_run:
        if st.button("🚀 지금 실행", use_container_width=True,
                     help="GitHub Actions 즉시 트리거 → 5분 내 메일 발송"):
            with st.spinner("GitHub Actions 트리거 중..."):
                ok, msg = trigger_github_action()
            if ok:
                st.success(f"🚀 {msg}")
                st.caption("진행 상황: https://github.com/" + (_secret("GITHUB_REPO") or "") + "/actions")
            else:
                st.error(f"❌ {msg}")

    with col_reset:
        if st.button("↩️ 취소", use_container_width=True):
            for k in [kw_key, ag_key, prof_key]:
                st.session_state.pop(k, None)
            st.rerun()

    # ── 위험 영역 (고객사 삭제) ──
    st.write("")
    st.write("")
    with st.expander("⚠️ 위험 영역 — 고객사 삭제", expanded=False):
        company_name = cfg.get("company_name", cid)
        st.markdown(
            f"""
            <div style="padding:12px;background:#fef2f2;border-left:4px solid #dc2626;
                        border-radius:4px;margin-bottom:12px;">
              <div style="font-weight:700;color:#dc2626;margin-bottom:6px;">⚠️ 되돌릴 수 없는 작업입니다</div>
              <div style="font-size:13px;color:#4b5563;line-height:1.6;">
                이 고객사의 <b>config.json + profile.json</b>이 영구 삭제되고 GitHub에서도 제거됩니다.<br>
                삭제 후에는 매일 자동 메일 발송에서 제외되며, 복구는 git 이력에서 수동 복원해야 합니다.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        confirm_input = st.text_input(
            f"확인을 위해 회사명을 정확히 입력하세요: **{company_name}**",
            placeholder=company_name,
            key=f"delete_confirm_{cid}",
        )
        can_delete = confirm_input.strip() == company_name.strip()

        if st.button(
            "🗑 고객사 영구 삭제",
            type="primary" if can_delete else "secondary",
            disabled=not can_delete,
            key=f"delete_btn_{cid}",
            help="회사명을 정확히 입력하면 활성화됩니다.",
        ):
            import shutil
            target = CUSTOMERS_DIR / cid
            try:
                shutil.rmtree(target)
                st.success(f"✅ '{company_name}' 로컬 폴더 삭제 완료")

                with st.spinner("GitHub에서도 제거 중..."):
                    ok, msg = git_commit_and_push(f"dashboard: delete customer {cid}")
                if ok:
                    st.success(f"☁️ {msg}")
                else:
                    st.warning(f"⚠️ {msg}")

                # 편집 페이지에서 빠져나가기
                for k in [kw_key, ag_key, prof_key, f"delete_confirm_{cid}"]:
                    st.session_state.pop(k, None)
                st.session_state["view"]    = "home"
                st.session_state["editing"] = None
                st.rerun()
            except Exception as e:
                st.error(f"삭제 실패: {e}")


# ─── 라우팅 ───────────────────────────────────────────────────

if st.session_state["view"] == "home":
    show_home()
elif st.session_state["view"] == "edit" and st.session_state["editing"]:
    show_edit(st.session_state["editing"])
else:
    st.session_state["view"] = "home"
    st.rerun()
