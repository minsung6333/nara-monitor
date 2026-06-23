# -*- coding: utf-8 -*-
"""
멀티 고객 실행 엔트리포인트.
customers/ 폴더의 모든 활성 고객을 순회하며 수집→분석→발송.

옵션:
  python main.py [YYYYMMDD] [--use-cache] [--no-mail]
    --use-cache : 캐시된 분석 결과 재사용 (LLM 호출 비용 0원)
    --no-mail   : 메일 발송 없이 HTML/Excel만 생성
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from collector import collect
from analyzer import run_pipeline, load_profile
from mailer import send_report

CACHE_DIR = Path(__file__).parent / "cache"


def _cache_path(date_str: str, cid: str) -> Path:
    return CACHE_DIR / date_str / f"{cid}.json"


def _load_cache(date_str: str, cid: str) -> dict | None:
    path = _cache_path(date_str, cid)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  [캐시 로드] {path.relative_to(Path(__file__).parent)}")
        return data
    except Exception as e:
        print(f"  [캐시 로드 실패] {e}")
        return None


def _save_cache(date_str: str, cid: str, notices: list, screened_all: list, results: list) -> None:
    path = _cache_path(date_str, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date_str":     date_str,
        "cid":          cid,
        "saved_at":     datetime.now().isoformat(),
        "notices":      notices,
        "screened_all": screened_all,
        "results":      results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"  [캐시 저장] {path.relative_to(Path(__file__).parent)}")


def _load_customers() -> list[dict]:
    customers_dir = Path("customers")
    if not customers_dir.exists():
        print("[오류] customers/ 폴더가 없습니다. onboard.py로 고객을 먼저 등록하세요.")
        return []

    result = []
    for d in sorted(customers_dir.iterdir()):
        if not d.is_dir():
            continue
        config_path  = d / "config.json"
        profile_path = d / "profile.json"
        if not config_path.exists() or not profile_path.exists():
            print(f"  [건너뜀] {d.name}: config.json 또는 profile.json 없음")
            continue
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        if not config.get("active", True):
            print(f"  [비활성] {d.name}")
            continue
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
        result.append({"id": d.name, "config": config, "profile": profile})
    return result


def _run_customer(customer: dict, date_str: str, days_back: int,
                  use_cache: bool = False, send_mail: bool = True) -> None:
    cid     = customer["id"]
    config  = customer["config"]
    profile = customer["profile"]

    keywords         = config.get("keywords") or []
    tracked_agencies = config.get("tracked_agencies") or []
    mail_to          = config.get("mail_to", "")
    company          = config.get("company_name", cid)

    print(f"\n{'#'*60}")
    print(f"# 고객: {company}  [{cid}]")
    print(f"{'#'*60}")

    notices = screened_all = results = None
    if use_cache:
        cached = _load_cache(date_str, cid)
        if cached:
            notices      = cached.get("notices", [])
            screened_all = cached.get("screened_all", [])
            results      = cached.get("results", [])
            print(f"  → 캐시 사용 (LLM 호출 0회): 수집 {len(notices)}건 · 분석 {len(results)}건")

    if notices is None:
        notices = collect(
            date_str,
            days_back=days_back,
            keywords=keywords,
            tracked_agencies=tracked_agencies,
        )
        results, screened_all = run_pipeline(notices, profile, return_screened=True)
        # 캐시 저장 (다음 실행에 재사용)
        _save_cache(date_str, cid, notices, screened_all, results)

    is_monday = now.weekday() == 6  # UTC 일요일 = KST 월요일
    subject = None
    if is_monday:
        dl = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}"
        subject = f"[나라장터 모니터] {dl} 금요일 AI 공고 분석 리포트"

    send_report(
        results, date_str,
        total_collected=len(notices),
        screened_all=screened_all,
        company_name=company,
        keywords=keywords,
        tracked_agencies=tracked_agencies,
        save=True,
        mail=send_mail,
        subject=subject,
        mail_to=mail_to,
        report_prefix=cid,
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    # 인자 파싱
    args = sys.argv[1:]
    use_cache = "--use-cache" in args
    send_mail = "--no-mail" not in args
    positional = [a for a in args if not a.startswith("--")]
    date_arg = positional[0] if positional else None

    now = datetime.now()
    # GitHub Actions는 UTC 기준: UTC 일요일(6) = KST 월요일
    # → 월요일 실행 시 금요일(2일 전) 공고 수집, 나머지는 전날
    if date_arg:
        date_str = date_arg
    elif now.weekday() == 6:  # UTC 일요일 = KST 월요일
        date_str = (now - timedelta(days=2)).strftime("%Y%m%d")  # 금요일
    else:
        date_str = now.strftime("%Y%m%d")

    days_back = 0

    customers = _load_customers()
    if not customers:
        sys.exit(1)

    day_label = "금요일" if now.weekday() == 6 else "전일"
    flags = []
    if use_cache: flags.append("USE_CACHE")
    if not send_mail: flags.append("NO_MAIL")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    print(f"\n고객 {len(customers)}개 처리 시작 — {date_str} ({day_label} 기준, days_back={days_back}){flag_str}")
    errors = []
    for customer in customers:
        try:
            _run_customer(customer, date_str, days_back,
                         use_cache=use_cache, send_mail=send_mail)
        except Exception as e:
            errors.append((customer["id"], e))
            print(f"\n[오류] {customer['id']}: {e}")

    print(f"\n{'='*60}")
    print(f"완료: {len(customers) - len(errors)}/{len(customers)}개 성공")
    if errors:
        for cid, err in errors:
            print(f"  실패: {cid} — {err}")
        sys.exit(1)
