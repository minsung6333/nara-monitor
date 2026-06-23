# -*- coding: utf-8 -*-
"""
2단계 분석 파이프라인
  1단계: 제목·메타 기반 배치 스크리닝 (LLM 1회 호출)
  2단계: HIGH/MED 항목만 PDF 다운로드 → 사업 개요 추출 → 상세 적합도 평가
"""
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from dotenv import load_dotenv

from downloader import download_files, select_files, EXTRACTABLE_EXTS
from extractor import extract_text_auto, extract_project_overview, format_overview_text

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MODEL_SCREEN = "gpt-5.4"
MODEL_ANALYZE = "gpt-5.4"
MAX_WORKERS   = 5   # 병렬 분석 동시 처리 수

# ── 프롬프트 ──────────────────────────────────────────────────

SCREEN_SYSTEM = """당신은 공공조달 입찰 전문가입니다.
회사 프로필과 공고 목록을 보고, 각 공고가 해당 회사에 적합한지 판단합니다.

판단 기준:
- HIGH: 회사의 핵심 역량과 직접 맞닿은 사업. 적극 검토 필요
- MED: 일부 역량이 겹치거나 컨소시엄·하도급으로 참여 가능한 사업
- LOW: 회사 역량과 무관하거나 exclusions에 해당하는 사업

반환 형식 (공고 수만큼 배열):
{"results": [
  {"idx": 0, "relevance": "HIGH", "reason": "한 줄 근거"},
  {"idx": 1, "relevance": "LOW",  "reason": "한 줄 근거"}
]}

규칙:
- 금액 0원·미공개는 감점 요인 아님 (사전규격·발주계획은 원래 없음)
- 발주계획은 아직 입찰 전 단계이므로 동일 사업이 나중에 입찰공고로도 뜰 수 있음
- 반드시 위 JSON만 반환"""

ANALYZE_SYSTEM = """당신은 공공조달 제안 전략 전문가입니다.
회사 프로필과 RFP 사업 개요를 바탕으로 적합도를 심층 분석합니다.

반환 형식:
{
  "score": 85,
  "verdict": "HIGH",
  "content_summary": "공고 원문 기반 사업 내용 요약 (무슨 사업인지 2~3문장, 회사 관점 제외)",
  "summary": "회사 적합도 관점의 핵심 분석 2~3문장",
  "match_points": ["강점 1", "강점 2", "강점 3"],
  "gap_points": ["리스크·부족한 점 1", "리스크 2"],
  "action": "추천 행동 (예: 즉시 검토 / 파트너사와 컨소시엄 검토 / 관망)"
}

규칙:
- score: 0~100 (회사가 수주 경쟁력을 갖출 가능성)
- verdict: HIGH(70+) / MED(40~69) / LOW(~39)
- content_summary: 공고/RFP 내용을 객관적으로 요약. 첨부문서 없으면 공고명·메타 기반으로 작성
- match_points: 회사 역량이 RFP 요건과 구체적으로 맞는 부분
- gap_points: 충족하기 어려운 요건, 리스크

[사업 개요(RFP)가 제공되지 않은 경우 — 매우 중요]
- 사업 개요가 "(첨부문서 없음 또는 추출 실패)"인 경우, 공고명·기관·금액 등 제한된 메타 정보만으로 판단해야 합니다.
- 이때는 실제 과업 내용·요구 기술·규모를 확인할 수 없으므로 적합성을 확신할 수 없습니다.
- 따라서 보수적으로 평가하세요: 메타 정보만으로 회사와 명백히 강하게 부합하는 예외적 경우가 아니라면 HIGH(70+)를 부여하지 마세요.
- 정보가 부족해 판단이 어려우면 MED 이하로 두고, gap_points에 "RFP 미확보로 상세 검토 필요"를 명시하세요.
- 점수를 인위적으로 깎으라는 의미가 아니라, 근거가 부족한 확신(특히 HIGH)을 피하라는 의미입니다.

- 반드시 위 JSON만 반환"""


# ── 1단계: 제목 스크리닝 ──────────────────────────────────────

def screen_titles(notices: list[dict], profile: dict) -> list[dict]:
    """
    전체 공고 제목·메타를 LLM에 한 번에 전송해 HIGH/MED/LOW 분류.
    반환: 각 notice에 'relevance', 'screen_reason' 필드 추가된 리스트
    """
    if not notices:
        return []

    profile_text = _format_profile(profile)

    lines = []
    for i, n in enumerate(notices):
        amt = f"{int(n['amount']):,}원" if n.get('amount') and str(n['amount']).isdigit() else "미공개"
        lines.append(
            f"[{i}] [{n['source']}·{n['type']}] {n['title']}\n"
            f"    기관: {n.get('agency', '')} | 금액: {amt}"
        )

    user_msg = f"## 회사 프로필\n{profile_text}\n\n## 공고 목록\n" + "\n\n".join(lines)

    try:
        resp = client.chat.completions.create(
            model=MODEL_SCREEN,
            messages=[
                {"role": "system", "content": SCREEN_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=120,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        results = {r["idx"]: r for r in data.get("results", [])}
    except Exception as e:
        print(f"  [스크리닝 실패] {e}")
        results = {}

    enriched = []
    for i, n in enumerate(notices):
        r = results.get(i, {})
        enriched.append({
            **n,
            "relevance":     r.get("relevance", "LOW"),
            "screen_reason": r.get("reason", ""),
        })
    return enriched


# ── 2단계: 문서 상세 분석 ─────────────────────────────────────

def analyze_notice(notice: dict, profile: dict, dest_dir: str = None) -> dict:
    """
    공고 1건: PDF 다운로드 → 사업 개요 추출 → 상세 적합도 평가.
    반환: notice에 'overview', 'analysis' 필드 추가

    동작:
    - RFP 문서(제안요청서/과업지시서) 없거나 사업 개요 추출 실패 시
      → 2단계 LLM 호출 스킵, 1단계 스크리닝 결과를 그대로 사용
    """
    files = notice.get("files", [])
    overview = {}

    if files:
        if dest_dir is None:
            dest_dir = tempfile.mkdtemp(prefix="nara_")

        downloaded = download_files(files, dest_dir)
        doc_files = [d for d in downloaded if d["ext"] in EXTRACTABLE_EXTS]

        for d in doc_files:
            try:
                pages = extract_text_auto(d["local_path"])
                if not pages:
                    continue
                overview = extract_project_overview(pages)
                if overview:
                    break
            except Exception as e:
                print(f"  [추출 실패] {d['name']}: {e}")

    # RFP 문서 추출 성공 → 사업 개요 기반 상세 분석
    # RFP 문서 없음/추출 실패 → 공고 제목·메타만으로 분석 (GPT 호출, overview는 빈 값)
    analysis = _analyze_with_llm(notice, overview, profile)

    # GPT 호출 실패 등으로 결과가 비면 1단계 스크리닝 결과로 보강
    if not analysis or not analysis.get("verdict"):
        relevance = notice.get("relevance", "MED")
        analysis = {
            "score":           {"HIGH": 75, "MED": 55, "LOW": 25}.get(relevance, 50),
            "verdict":         relevance,
            "content_summary": notice.get("title", ""),
            "summary":         notice.get("screen_reason", "1단계 스크리닝 결과 사용"),
            "match_points":    [],
            "gap_points":      [],
            "action":          "공고 본문 직접 확인 필요",
        }
    if not overview:
        analysis["_no_rfp"] = True  # RFP 없이 메타 기반 분석임을 표시
    return {**notice, "overview": overview, "analysis": analysis}


def _analyze_with_llm(notice: dict, overview: dict, profile: dict) -> dict:
    profile_text = _format_profile(profile)
    overview_text = format_overview_text(overview) if overview else "(첨부문서 없음 또는 추출 실패)"

    amt = f"{int(notice['amount']):,}원" if notice.get('amount') and str(notice['amount']).isdigit() else "미공개"
    notice_meta = (
        f"공고명: {notice.get('title', '')}\n"
        f"출처: {notice.get('source', '')} / {notice.get('type', '')}\n"
        f"기관: {notice.get('agency', '')}\n"
        f"금액: {amt}\n"
        f"마감: {notice.get('close_date', '미정')}"
    )

    user_msg = (
        f"## 회사 프로필\n{profile_text}\n\n"
        f"## 공고 메타\n{notice_meta}\n\n"
        f"## 사업 개요 (RFP 도입부 추출)\n{overview_text}"
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL_ANALYZE,
            messages=[
                {"role": "system", "content": ANALYZE_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=120,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        print(f"  [분석 실패] {e}")
        return {}


# ── 전체 파이프라인 ───────────────────────────────────────────

def run_pipeline(
    notices: list[dict],
    profile: dict,
    screen_threshold: tuple[str, ...] = ("HIGH", "MED"),
    dest_dir: str = None,
    return_screened: bool = False,
) -> list[dict] | tuple[list[dict], list[dict]]:
    """
    notices: collector.collect() 결과
    profile: profile.json 내용
    screen_threshold: 2단계 분석으로 넘길 relevance 값
    return_screened: True면 (results, screened_all) 튜플 반환
    반환: 최종 분석 결과 리스트 (score 기준 내림차순 정렬)
    """
    print(f"\n[1단계] 제목 스크리닝 — {len(notices)}건")
    screened = screen_titles(notices, profile)

    candidates = [n for n in screened if n["relevance"] in screen_threshold]
    skipped    = len(notices) - len(candidates)
    print(f"  → HIGH/MED: {len(candidates)}건 | LOW(제외): {skipped}건")

    if not candidates:
        return ([], screened) if return_screened else []

    print(f"\n[2단계] 문서 분석 — {len(candidates)}건 (병렬 {MAX_WORKERS}개)")
    base_dir = dest_dir or tempfile.mkdtemp(prefix="nara_docs_")

    def _analyze_one(args):
        idx, total, notice = args
        # 스레드별 독립 디렉터리로 파일 충돌 방지
        sub = os.path.join(base_dir, f"n{idx:03d}")
        os.makedirs(sub, exist_ok=True)
        has_files = bool(notice.get("files"))
        print(f"  [{idx}/{total}] {notice['title'][:45]}... (파일: {'있음' if has_files else '없음'})")
        analyzed = analyze_notice(notice, profile, sub)
        score = analyzed.get("analysis", {}).get("score", 0)
        print(f"    [{idx}] → {score}점 / {analyzed.get('analysis', {}).get('verdict', '?')}")
        return analyzed

    total = len(candidates)
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_analyze_one, (i, total, notice)): i
            for i, notice in enumerate(candidates, 1)
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                print(f"  [분석 오류] {e}")

    results.sort(key=lambda x: x.get("analysis", {}).get("score", 0), reverse=True)
    print(f"\n파이프라인 완료: {len(results)}건 분석")
    return (results, screened) if return_screened else results


# ── 유틸 ──────────────────────────────────────────────────────

_PROFILE_SKIP = {"company_name", "description", "min_amount", "notes"}
_PROFILE_LABELS = {
    "business_areas":    "사업 영역",
    "core_technologies": "핵심 기술",
    "certifications":    "인증·파트너십",
    "target_sectors":    "주요 고객 섹터",
    "references":        "주요 레퍼런스",
    "exclusions":        "제외 영역 (관심 없음)",
    # 추가 필드는 key를 그대로 사용 (snake_case → 공백 치환)
}


def _format_profile(profile: dict) -> str:
    """profile.json의 모든 리스트 필드를 동적으로 GPT 프롬프트에 포함."""
    lines = [f"회사명: {profile.get('company_name', '')}"]
    if d := profile.get("description"):
        lines.append(f"소개: {d}")

    # 커스텀 필드의 한국어 레이블 매핑 (LLM이 생성)
    custom_labels = profile.get("_labels") or {}

    for key, value in profile.items():
        if key in _PROFILE_SKIP or key.startswith("_"):
            continue
        if not isinstance(value, list) or not value:
            continue
        label = (
            _PROFILE_LABELS.get(key)
            or custom_labels.get(key)
            or key.replace("_", " ")
        )
        lines.append(f"{label}:\n" + "\n".join(f"  - {v}" for v in value))

    if notes := profile.get("notes"):
        lines.append(f"특이사항: {notes}")
    return "\n".join(lines)


def load_profile(path: str = "profile.json") -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    from collector import collect

    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    profile  = load_profile()
    notices  = collect(date_arg)
    results  = run_pipeline(notices, profile)

    print("\n\n=== 최종 결과 ===")
    for r in results:
        a = r.get("analysis", {})
        amt = f"{int(r['amount']):,}원" if r.get('amount') and str(r['amount']).isdigit() else "미공개"
        print(f"\n[{a.get('score', '?')}점·{a.get('verdict', '?')}] {r['title']}")
        print(f"  기관: {r.get('agency')} | 금액: {amt}")
        print(f"  요약: {a.get('summary', '')}")
        print(f"  행동: {a.get('action', '')}")
