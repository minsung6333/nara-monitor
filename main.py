# -*- coding: utf-8 -*-
"""
멀티 고객 실행 엔트리포인트.
customers/ 폴더의 모든 활성 고객을 순회하며 수집→분석→발송.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from collector import collect
from analyzer import run_pipeline, load_profile
from mailer import send_report


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


def _run_customer(customer: dict, date_str: str, days_back: int) -> None:
    cid     = customer["id"]
    config  = customer["config"]
    profile = customer["profile"]

    keywords    = config.get("keywords") or []
    mail_to     = config.get("mail_to", "")
    company     = config.get("company_name", cid)

    print(f"\n{'#'*60}")
    print(f"# 고객: {company}  [{cid}]")
    print(f"{'#'*60}")

    notices = collect(date_str, days_back=days_back, keywords=keywords)
    results, screened_all = run_pipeline(notices, profile, return_screened=True)

    is_monday = datetime.now().weekday() == 0
    subject = None
    if is_monday:
        dl = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}"
        subject = f"[나라장터 모니터] {dl} 주간 AI 공고 분석 리포트 (금~월)"

    send_report(
        results, date_str,
        total_collected=len(notices),
        screened_all=screened_all,
        company_name=company,
        keywords=keywords,
        save=True,
        mail=True,
        subject=subject,
        mail_to=mail_to,
        report_prefix=cid,
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    date_str = date_arg or datetime.now().strftime("%Y%m%d")

    days_back = 1

    customers = _load_customers()
    if not customers:
        sys.exit(1)

    print(f"\n고객 {len(customers)}개 처리 시작 — {date_str} (days_back={days_back})")
    errors = []
    for customer in customers:
        try:
            _run_customer(customer, date_str, days_back)
        except Exception as e:
            errors.append((customer["id"], e))
            print(f"\n[오류] {customer['id']}: {e}")

    print(f"\n{'='*60}")
    print(f"완료: {len(customers) - len(errors)}/{len(customers)}개 성공")
    if errors:
        for cid, err in errors:
            print(f"  실패: {cid} — {err}")
        sys.exit(1)
