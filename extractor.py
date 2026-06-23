# -*- coding: utf-8 -*-
"""
문서 텍스트 추출 (PDF / HWP / HWPX) + 제안요청서 앞부분 사업 개요 파싱
rfp-writer/dist/modules/parser.py 에서 필요한 부분만 발췌·개선
"""
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MODEL = "gpt-5.4"

# ── 프롬프트 ──────────────────────────────────────────────────

PROJECT_OVERVIEW_SYSTEM = """당신은 RFP 문서 분석 전문가입니다.
주어진 RFP 도입부 텍스트에서 사업의 핵심 컨텍스트를 추출해 아래 JSON으로 반환하세요.

이 내용은 이후 제안서 작성 전 과정에서 컨텍스트로 활용되므로, 원문의 정보를 최대한 풍부하게 보존해야 합니다.
한 줄 요약이 아니라, 해당 항목과 관련된 모든 문장·수치·항목을 빠짐없이 옮겨야 합니다.

{
  "project_name": "사업명 전체",
  "ordering_agency": "발주기관명 (주관기관·수요기관·감리기관 포함, 확인 가능한 모두)",
  "background": "추진 배경 — 관련 정책·법령·현황·문제점 등 원문 전체 보존. 단락·항목 구조 유지",
  "current_status": "현황 분석 — 기존 시스템·서비스·인프라·조직 현황. 수치·명칭 포함. 없으면 빈 문자열",
  "necessity": "추진 필요성 — 필요성 항목이 여러 개면 모두 포함. 수치·근거 그대로",
  "purpose": "사업 목적 — 원문 전체. 여러 목적 항목 모두 포함",
  "goals": "주요 목표 — bullet 형태로 누락 없이. 세부 설명·수치·지표까지 포함",
  "scope": "사업 범위 — 구축 범위·과업 내용·제외 범위·시스템 구성요소를 상세히",
  "deliverables": "주요 산출물·납품물 목록 (보고서, SW, 매뉴얼 등). 없으면 빈 문자열",
  "duration_budget": "사업 기간(시작~종료)·총 예산·계약 방식·부가세 포함 여부 등 모든 수치",
  "legal_basis": "근거 법령·지침·규정·고시 명칭. 원문 그대로. 없으면 빈 문자열",
  "tech_stack": "언급된 기술·플랫폼·표준·프레임워크·언어·API 등. 없으면 빈 문자열",
  "key_points": "보안등급·인증요건·특수조건·평가 가중치·필수 자격요건 등 제안사가 반드시 알아야 할 모든 제약"
}

원칙:
- 요약 금지 — 원문 정보를 최대한 풍부하게 보존 (군더더기·중복 표현만 제거)
- 표/항목 나열이 있으면 그 구조 유지 ('ㅇ', '▢', '①' → '-' bullet 변환)
- 숫자·고유명사·시스템명·기술명·근거 법령은 반드시 그대로 포함
- 여러 단락에 걸친 내용도 해당 필드에 모두 수집
- 원문에 없는 정보는 빈 문자열. 추측 금지
- 각 필드별 길이 상한 없음
- 반드시 JSON만 반환"""

# ── 요구사항 섹션 시작 감지용 패턴 (개요 범위 결정) ──────────

_REQ_KEYWORDS_STRICT = [
    "요구사항 고유번호", "요구사항 ID", "요구사항ID",
    "SFR-", "DAR-", "SER-", "ECR-", "SIR-", "QUR-",
    "TER-", "PMR-", "PSR-", "PER-", "NFR-", "OTR-", "MNT-",
]
_REQ_ID_PAT = re.compile(r'\b[A-Z]{2,4}-\d{2,4}\b')

OVERVIEW_KEYWORDS = [
    "사업 개요", "사업개요", "사업명", "사업 명",
    "추진 배경", "추진배경", "사업 배경", "사업배경",
    "추진 필요성", "추진필요성", "필요성",
    "사업 목적", "사업목적", "추진 목적",
    "사업 목표", "사업목표", "추진 목표",
    "사업 범위", "사업범위", "과업 범위", "과업범위",
    "사업 기간", "사업기간", "사업 예산", "사업예산",
    "현황", "추진 경위", "법적 근거",
]

# ── 텍스트 추출 ───────────────────────────────────────────────

def _is_space_broken(text: str) -> bool:
    """한글 연속 비율이 높으면 공백 깨짐으로 판단."""
    korean_runs = re.findall(r'[가-힣]{6,}', text)
    korean_chars = sum(len(r) for r in korean_runs)
    total_korean = len(re.findall(r'[가-힣]', text))
    return total_korean > 80 and korean_chars / max(total_korean, 1) > 0.4


def extract_text_by_page(pdf_path: str) -> list[dict]:
    """PyMuPDF로 전체 페이지 추출. 공백 깨짐 감지 시 pdfplumber로 재추출."""
    fitz_pages = []
    with fitz.open(pdf_path) as pdf:
        for i, page in enumerate(pdf):
            fitz_pages.append({"page": i + 1, "text": page.get_text() or ""})

    n = len(fitz_pages)
    sample = fitz_pages[max(1, n // 5): min(n, n // 5 + 10)]
    need_plumber = any(
        _is_space_broken(p["text"]) for p in sample
        if p["text"].strip() and len(p["text"]) > 200
    )

    if not need_plumber:
        return fitz_pages

    try:
        with pdfplumber.open(pdf_path) as pdf:
            plumber_map = {i + 1: (page.extract_text() or "") for i, page in enumerate(pdf.pages)}
        return [{"page": p["page"], "text": plumber_map.get(p["page"], p["text"])} for p in fitz_pages]
    except Exception:
        return fitz_pages


# ── HWP / HWPX 텍스트 추출 ────────────────────────────────────

# 한글 본문에 섞여 들어오는 인라인 컨트롤·바이너리 노이즈 제거
_NOISE_PAT = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')
# CJK 호환·사용자영역 등 깨진 글리프(예: 捤獥汤捯) 제거용 — 한글/한자/영숫자/일반기호만 허용
_KEEP_PAT = re.compile(
    r'[가-힣ㄱ-ㅎㅏ-ㅣ'           # 한글
    r'a-zA-Z0-9'                    # 영숫자
    r'一-鿿'                # 한자(CJK 통합)
    r'\s'                           # 공백
    r'.,·:;~!?@#%&*()\[\]{}<>/\\\-_=+\'"“”‘’『』「」【】◁▷①-⑳ㅇㆍ▢□■○●△▲%원년월일]+'
)


def _clean_hwp_text(text: str) -> str:
    """HWP 추출 텍스트에서 제어문자·깨진 글리프 노이즈 제거."""
    text = _NOISE_PAT.sub(' ', text)
    # 허용 문자만 추려서 이어붙임 (깨진 CJK 글리프 토막 제거)
    text = ' '.join(_KEEP_PAT.findall(text))
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# HWP/HWPX는 페이지 개념이 없어 한 덩어리로 추출됨.
# PDF 평균 페이지 분량(~1800자)으로 잘라 가상 페이지를 만들어
# 페이지 기반 사업개요 추출 로직(extract_project_overview)과 호환시킨다.
_VIRTUAL_PAGE_SIZE = 1800


def _paginate(text: str, size: int = _VIRTUAL_PAGE_SIZE) -> list[dict]:
    """긴 텍스트를 가상 페이지 리스트 [{page, text}]로 분할."""
    if not text:
        return []
    pages = []
    for idx, start in enumerate(range(0, len(text), size), 1):
        pages.append({"page": idx, "text": text[start:start + size]})
    return pages


def extract_text_hwpx(path: str) -> list[dict]:
    """HWPX(ZIP+OWPML XML) 텍스트 추출. PDF와 동일하게 [{page, text}] 반환."""
    try:
        with zipfile.ZipFile(path) as z:
            section_names = sorted(
                n for n in z.namelist()
                if 'section' in n.lower() and n.endswith('.xml')
            )
            parts = []
            for n in section_names:
                raw = z.read(n).decode('utf-8', errors='ignore')
                # <hp:t>...</hp:t> 등 텍스트 노드 사이 내용만 남기고 태그 제거
                txt = re.sub(r'<[^>]+>', ' ', raw)
                parts.append(txt)
        full = _clean_hwp_text(' '.join(parts))
        return _paginate(full)
    except Exception as e:
        print(f"  [HWPX 추출 실패] {Path(path).name}: {e}")
        return []


def extract_text_hwp(path: str) -> list[dict]:
    """HWP(OLE 복합 + zlib) 본문 텍스트 추출. PDF와 동일하게 [{page, text}] 반환."""
    try:
        import olefile
    except ImportError:
        print("  [HWP 추출 실패] olefile 미설치: pip install olefile")
        return []

    try:
        ole = olefile.OleFileIO(path)
        header = ole.openstream('FileHeader').read()
        compressed = bool(header[36] & 1)

        sections = sorted(e for e in ole.listdir() if e and e[0] == 'BodyText')
        parts = []
        for entry in sections:
            data = ole.openstream(entry).read()
            if compressed:
                data = zlib.decompress(data, -15)
            parts.append(_parse_hwp_records(data))
        ole.close()

        full = _clean_hwp_text(' '.join(parts))
        return _paginate(full)
    except Exception as e:
        print(f"  [HWP 추출 실패] {Path(path).name}: {e}")
        return []


def _parse_hwp_records(data: bytes) -> str:
    """HWP BodyText 섹션의 레코드 스트림에서 HWPTAG_PARA_TEXT(67) 추출."""
    text = []
    i = 0
    n = len(data)
    while i < n - 4:
        rec = struct.unpack('<I', data[i:i+4])[0]
        tag_id = rec & 0x3ff
        size = (rec >> 20) & 0xfff
        i += 4
        if size == 0xfff:
            if i + 4 > n:
                break
            size = struct.unpack('<I', data[i:i+4])[0]
            i += 4
        payload = data[i:i+size]
        i += size
        if tag_id == 67:  # HWPTAG_PARA_TEXT
            text.append(_decode_para_text(payload))
    return '\n'.join(text)


# HWP PARA_TEXT 인라인 컨트롤 문자 분류 (WCHAR 단위)
#  - 개행류(1 WCHAR): 10, 13
#  - 무시(1 WCHAR): 0, 24~31
#  - 인라인/확장 컨트롤(8 WCHAR = 16 byte 점유): 그 외 1~31
_HWP_CTRL_1WCHAR = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}


def _decode_para_text(payload: bytes) -> str:
    """PARA_TEXT WCHAR 배열을 디코딩하며 컨트롤 코드를 규격대로 건너뜀."""
    out = []
    j = 0
    L = len(payload) - (len(payload) % 2)
    while j < L:
        code = payload[j] | (payload[j+1] << 8)
        if code < 32:
            if code in (10, 13):
                out.append('\n')
                j += 2
            elif code in _HWP_CTRL_1WCHAR:
                j += 2
            else:
                # 인라인/확장 컨트롤: 8 WCHAR(16 byte) 점유
                j += 16
        else:
            out.append(chr(code))
            j += 2
    return ''.join(out)


def extract_text_auto(path: str) -> list[dict]:
    """확장자에 따라 적절한 추출기 선택. [{page, text}] 반환."""
    ext = Path(path).suffix.lower()
    if ext == '.pdf':
        return extract_text_by_page(path)
    if ext == '.hwpx':
        return extract_text_hwpx(path)
    if ext == '.hwp':
        return extract_text_hwp(path)
    return []


# ── 사업 개요 추출 ────────────────────────────────────────────

def extract_project_overview(pages: list[dict]) -> dict:
    """RFP 도입부에서 사업 개요 추출."""
    # 요구사항 섹션 시작 페이지 찾기 → 그 이전까지만 스캔
    req_start = None
    for p in pages:
        if any(k in p["text"] for k in _REQ_KEYWORDS_STRICT) or len(_REQ_ID_PAT.findall(p["text"])) >= 2:
            req_start = p["page"]
            break

    # 요구사항 섹션 직전까지를 사업개요 범위로. 단 최소 3페이지는 보장
    # (요구사항 ID·키워드가 도입부에서 일찍 감지돼 limit=0이 되는 경우 방지)
    limit = min((req_start - 1) if req_start else 30, 30)
    limit = max(limit, 3)
    candidate_pages = [p for p in pages if p["page"] <= limit]

    keyword_pages = [p for p in candidate_pages if any(k in p["text"] for k in OVERVIEW_KEYWORDS)]
    target_pages = keyword_pages or candidate_pages

    # 그래도 비면 문서 앞부분(최대 5페이지)을 fallback으로 사용
    if not target_pages:
        target_pages = pages[:5]
    if not target_pages:
        return {}

    text = "\n\n---PAGE---\n\n".join(f"[p{p['page']}]\n{p['text']}" for p in target_pages)
    if len(text) > 80000:
        text = text[:80000]

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": PROJECT_OVERVIEW_SYSTEM},
                {"role": "user", "content": text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=120,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        print(f"사업 개요 추출 실패: {e}")
        return {}


def format_overview_text(overview: dict) -> str:
    """LLM 컨텍스트 또는 이메일 본문용으로 사업 개요를 텍스트로 포매팅."""
    if not overview:
        return ""
    labels = {
        "project_name":   "사업명",
        "ordering_agency": "발주기관",
        "background":     "추진 배경",
        "current_status": "현황",
        "necessity":      "추진 필요성",
        "purpose":        "사업 목적",
        "goals":          "주요 목표",
        "scope":          "사업 범위",
        "deliverables":   "산출물",
        "duration_budget": "기간·예산",
        "legal_basis":    "법적 근거",
        "tech_stack":     "기술 스택",
        "key_points":     "핵심 제약·요건",
    }
    parts = []
    for key, label in labels.items():
        v = overview.get(key, "").strip()
        if v:
            parts.append(f"**{label}**\n{v}")
    return "\n\n".join(parts)


def parse_pdf_overview(pdf_path: str) -> dict:
    """PDF 한 건에서 텍스트 추출 → 사업 개요 반환."""
    pages = extract_text_by_page(pdf_path)
    return extract_project_overview(pages)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python extractor.py <pdf_path>")
        sys.exit(1)

    overview = parse_pdf_overview(sys.argv[1])
    print(format_overview_text(overview))
