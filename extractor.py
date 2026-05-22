# -*- coding: utf-8 -*-
"""
PDF 텍스트 추출 + 제안요청서 앞부분 사업 개요 파싱
rfp-writer/dist/modules/parser.py 에서 필요한 부분만 발췌·개선
"""
import json
import re
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


# ── 사업 개요 추출 ────────────────────────────────────────────

def extract_project_overview(pages: list[dict]) -> dict:
    """RFP 도입부에서 사업 개요 추출."""
    # 요구사항 섹션 시작 페이지 찾기 → 그 이전까지만 스캔
    req_start = None
    for p in pages:
        if any(k in p["text"] for k in _REQ_KEYWORDS_STRICT) or len(_REQ_ID_PAT.findall(p["text"])) >= 2:
            req_start = p["page"]
            break

    limit = min((req_start - 1) if req_start else 30, 30)
    candidate_pages = [p for p in pages if p["page"] <= limit]

    keyword_pages = [p for p in candidate_pages if any(k in p["text"] for k in OVERVIEW_KEYWORDS)]
    target_pages = keyword_pages or candidate_pages

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
