# -*- coding: utf-8 -*-
"""
신규 고객 등록 스크립트.

사용법:
  python onboard.py                        # 대화형 입력
  python onboard.py --pdf 소개서.pdf       # 소개서 자동 분석
  python onboard.py --id acme --pdf ...    # 고객 ID 직접 지정

실행하면 customers/<id>/ 폴더에 config.json + profile.json 생성됩니다.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ── 유틸 ──────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """회사명 → 폴더 ID (영문 소문자 + 숫자 + 하이픈)"""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_]+", "-", text)
    return text or "customer"


def _ask(prompt: str, default: str = "") -> str:
    val = input(f"{prompt} [{default}]: ").strip() if default else input(f"{prompt}: ").strip()
    return val or default


# ── PDF → profile.json 자동 생성 ─────────────────────────────

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

## 규칙
- 없는 항목은 빈 배열 [] 또는 빈 문자열로
- 추가 필드의 value는 반드시 문자열 배열 형태로
- 추가 필드는 반드시 "_labels"에 한국어 표시명 매핑 등록
- 반드시 JSON만 반환 (설명 텍스트 없이)"""


def _extract_profile_from_pdf(pdf_path: str) -> dict:
    try:
        import fitz
    except ImportError:
        print("  [오류] PyMuPDF 미설치: pip install pymupdf")
        return {}

    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    doc = fitz.open(pdf_path)
    pages_text = [doc[i].get_text() for i in range(min(len(doc), 30))]
    full_text = "\n\n".join(pages_text)[:60000]
    doc.close()

    print(f"  PDF 읽기 완료 ({len(pages_text)}페이지, {len(full_text):,}자) → LLM 분석 중...")
    try:
        resp = client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": PROFILE_SYSTEM},
                {"role": "user",   "content": full_text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=120,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        print(f"  [LLM 오류] {e}")
        return {}


# ── 메인 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="나라장터 모니터 고객 등록")
    parser.add_argument("--id",  help="고객 ID (폴더명, 영문 소문자·하이픈)")
    parser.add_argument("--pdf", help="회사 소개서 PDF 경로")
    args = parser.parse_args()

    print("\n=== 나라장터 모니터 — 신규 고객 등록 ===\n")

    # 1. 회사명
    company_name = _ask("회사명")
    if not company_name:
        print("[오류] 회사명은 필수입니다.")
        sys.exit(1)

    # 2. 고객 ID
    default_id = _slugify(company_name)
    customer_id = args.id or _ask("고객 ID (폴더명, 영문)", default_id)
    customer_id = re.sub(r"[^\w-]", "", customer_id).lower() or default_id

    dest = Path("customers") / customer_id
    if dest.exists():
        overwrite = _ask(f"  '{customer_id}' 폴더가 이미 존재합니다. 덮어쓸까요? (y/n)", "n")
        if overwrite.lower() != "y":
            print("취소합니다.")
            sys.exit(0)

    # 3. 키워드
    print("\n검색 키워드를 쉼표로 구분해 입력하세요.")
    kw_input = _ask("키워드", "AI,인공지능,데이터,소프트웨어")
    keywords = [k.strip() for k in kw_input.split(",") if k.strip()]

    # 4. 수신 이메일
    mail_to = _ask("리포트 수신 이메일 (복수면 쉼표 구분)")
    if not mail_to:
        print("[오류] 수신 이메일은 필수입니다.")
        sys.exit(1)

    # 5. profile.json 생성
    pdf_path = args.pdf or _ask("소개서 PDF 경로 (없으면 Enter 건너뜀)", "")
    if pdf_path and Path(pdf_path).exists():
        print(f"\n소개서 분석 중: {pdf_path}")
        profile = _extract_profile_from_pdf(pdf_path)
        if profile:
            profile.setdefault("company_name", company_name)
            print("  프로필 자동 생성 완료.")
        else:
            print("  자동 생성 실패 — 기본 템플릿으로 생성합니다.")
            profile = _blank_profile(company_name)
    else:
        if pdf_path:
            print(f"  [경고] PDF 파일을 찾을 수 없습니다: {pdf_path}")
        print("  기본 템플릿으로 profile.json을 생성합니다. 나중에 직접 편집하세요.")
        profile = _blank_profile(company_name)

    # 6. 저장
    dest.mkdir(parents=True, exist_ok=True)

    config = {
        "company_name": company_name,
        "keywords": keywords,
        "mail_to": mail_to,
        "active": True,
    }
    with open(dest / "config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    with open(dest / "profile.json", "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    print(f"\n등록 완료: customers/{customer_id}/")
    print(f"  config.json  — 키워드: {keywords}, 수신: {mail_to}")
    print(f"  profile.json — {'자동 생성' if pdf_path else '템플릿 (직접 편집 필요)'}")
    if not pdf_path:
        print(f"\n  나중에 PDF가 생기면: python onboard.py --id {customer_id} --pdf 소개서.pdf")


def _blank_profile(company_name: str) -> dict:
    return {
        "company_name": company_name,
        "description": "",
        "business_areas": [],
        "core_technologies": [],
        "certifications": [],
        "target_sectors": [],
        "exclusions": [],
        "notes": "",
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
