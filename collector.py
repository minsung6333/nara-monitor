import requests
from datetime import datetime, timedelta
from config import SERVICE_KEY, KEYWORDS, ENDPOINTS, COLLECT_SERVC, COLLECT_THNG, COLLECT_CNSTWK

ROWS_PER_PAGE = 100


def _fetch_all_pages(url: str, base_params: dict) -> list:
    """페이지네이션 처리해서 전체 결과 반환"""
    all_items = []
    page = 1

    while True:
        params = {**base_params, 'pageNo': str(page), 'numOfRows': str(ROWS_PER_PAGE)}
        try:
            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print(f"    [오류] {url} / 페이지 {page}: {e}")
            break

        header = data['response']['header']
        if header['resultCode'] != '00':
            print(f"    [API 오류] {header['resultMsg']}")
            break

        body = data['response']['body']
        total = int(body.get('totalCount', 0))
        items = body.get('items', [])

        if not items:
            break
        if isinstance(items, dict):
            items = [items]  # 1건일 때 dict로 오는 경우

        all_items.extend(items)

        if len(all_items) >= total:
            break
        page += 1

    return all_items


def _keyword_match(text: str, keywords: list = None) -> bool:
    """키워드 중 하나라도 포함되면 True"""
    if not text:
        return False
    kws = keywords if keywords is not None else KEYWORDS
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in kws)


def _extract_files_bid(item: dict) -> list:
    """입찰공고 응답에서 첨부파일 목록 추출"""
    files = []
    for i in range(1, 6):
        url  = item.get(f'ntceSpecDocUrl{i}', '')
        name = item.get(f'ntceSpecFileNm{i}', '')
        if url:
            files.append({'name': name or f'첨부파일{i}', 'url': url})
    return files


def _extract_files_spec(item: dict) -> list:
    """사전규격 응답에서 첨부파일 목록 추출"""
    files = []
    for i in range(1, 6):
        url = item.get(f'specDocFileUrl{i}', '')
        if url:
            files.append({'name': f'규격문서{i}', 'url': url})
    return files


def _normalize_bid(item: dict, biz_type: str) -> dict:
    """입찰공고 항목을 공통 형식으로 변환"""
    return {
        'source': '입찰공고',
        'type': biz_type,
        'title': item.get('bidNtceNm', ''),
        'agency': item.get('ntceInsttNm', ''),
        'demand_agency': item.get('dminsttNm', ''),
        'amount': item.get('asignBdgtAmt', ''),
        'reg_date': item.get('bidNtceDt', ''),
        'close_date': item.get('bidClseDt', ''),
        'open_date': item.get('opengDt', ''),
        'contract_method': item.get('cntrctCnclsMthdNm', ''),
        'detail_url': item.get('bidNtceDtlUrl', ''),
        'notice_no': item.get('bidNtceNo', ''),
        'files': _extract_files_bid(item),
    }


def _normalize_spec(item: dict, biz_type: str) -> dict:
    """사전규격 항목을 공통 형식으로 변환"""
    return {
        'source': '사전규격',
        'type': biz_type,
        'title': item.get('prdctClsfcNoNm', ''),
        'agency': item.get('orderInsttNm', ''),
        'demand_agency': item.get('rlDminsttNm', ''),
        'amount': item.get('asignBdgtAmt', ''),
        'reg_date': item.get('rcptDt', ''),
        'close_date': item.get('opninRgstClseDt', ''),
        'open_date': '',
        'contract_method': '',
        'detail_url': '',
        'notice_no': item.get('bfSpecRgstNo', ''),
        'files': _extract_files_spec(item),
    }


def _normalize_plan(item: dict, biz_type: str) -> dict:
    """발주계획 항목을 공통 형식으로 변환"""
    # 연결된 입찰공고번호 목록 파싱 (예: "R26BK01539068000,R26BK01539069000")
    bid_list_raw = item.get('bidNtceNoList', '') or ''
    linked_bid_nos = [b.strip() for b in bid_list_raw.split(',') if b.strip()]

    return {
        'source': '발주계획',
        'type': biz_type,
        'title': item.get('bizNm', ''),
        'agency': item.get('orderInsttNm', ''),
        'demand_agency': item.get('totlmngInsttNm', ''),
        'amount': str(item.get('sumOrderAmt', '')),
        'reg_date': item.get('nticeDt', ''),
        'close_date': '',
        'open_date': '',
        'contract_method': item.get('cntrctMthdNm', ''),
        'detail_url': '',
        'notice_no': item.get('orderPlanUntyNo', ''),
        'linked_bid_nos': linked_bid_nos,  # 연결된 입찰공고번호 (이미 공고된 경우)
        'files': [],  # 발주계획 자체에는 파일 없음
    }


# ─────────────────────────────────────────────────────────────
# 입찰공고 수집 (API 레벨에서 키워드 필터링)
# ─────────────────────────────────────────────────────────────

def collect_bid_notices(date_str: str, days_back: int = 1, keywords: list = None) -> list:
    """
    date_str: 'YYYYMMDD' 형식 (예: '20260522')
    days_back: 수집 시작일을 며칠 전으로 잡을지 (월요일이면 3)
    keywords: 고객별 키워드 (None이면 config.py KEYWORDS 사용)
    키워드별로 API 호출 → 결과 합산 후 중복 제거
    """
    kws = keywords if keywords is not None else KEYWORDS
    results = []
    seen = set()
    prev_day = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=days_back)).strftime('%Y%m%d')
    date_start = prev_day + '0000'
    date_end   = date_str + '2359'

    targets = []
    if COLLECT_SERVC:  targets.append(('bid_servc',  '용역'))
    if COLLECT_THNG:   targets.append(('bid_thng',   '물품'))
    if COLLECT_CNSTWK: targets.append(('bid_cnstwk', '공사'))

    for endpoint_key, biz_type in targets:
        for keyword in kws:
            print(f"  [입찰공고/{biz_type}] 키워드: '{keyword}' 수집 중...")
            params = {
                'ServiceKey': SERVICE_KEY,
                'type': 'json',
                'inqryDiv': '1',
                'inqryBgnDt': date_start,
                'inqryEndDt': date_end,
                'bidNtceNm': keyword,
            }
            items = _fetch_all_pages(ENDPOINTS[endpoint_key], params)
            for item in items:
                uid = item.get('bidNtceNo', '') + item.get('bidNtceOrd', '')
                if uid and uid not in seen:
                    seen.add(uid)
                    results.append(_normalize_bid(item, biz_type))

    return results


# ─────────────────────────────────────────────────────────────
# 사전규격 수집 (전체 수집 후 로컬 키워드 필터링)
# ─────────────────────────────────────────────────────────────

def collect_pre_specs(date_str: str, days_back: int = 1, keywords: list = None) -> list:
    """
    전체 사전규격 수집 후 제목(prdctClsfcNoNm) 기준으로 키워드 필터링
    """
    kws = keywords if keywords is not None else KEYWORDS
    results = []
    seen = set()
    prev_day = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=days_back)).strftime('%Y%m%d')
    date_start = prev_day + '0000'
    date_end   = date_str + '2359'

    targets = []
    if COLLECT_SERVC:  targets.append(('spec_servc',  '용역'))
    if COLLECT_THNG:   targets.append(('spec_thng',   '물품'))
    if COLLECT_CNSTWK: targets.append(('spec_cnstwk', '공사'))

    for endpoint_key, biz_type in targets:
        print(f"  [사전규격/{biz_type}] 전체 수집 중...")
        params = {
            'ServiceKey': SERVICE_KEY,
            'type': 'json',
            'inqryDiv': '1',
            'inqryBgnDt': date_start,
            'inqryEndDt': date_end,
        }
        items = _fetch_all_pages(ENDPOINTS[endpoint_key], params)
        print(f"    → {len(items)}건 수집, 키워드 필터링 중...")

        for item in items:
            title = item.get('prdctClsfcNoNm', '')
            if not _keyword_match(title, kws):
                continue
            uid = item.get('bfSpecRgstNo', '')
            if uid and uid not in seen:
                seen.add(uid)
                results.append(_normalize_spec(item, biz_type))

    return results


# ─────────────────────────────────────────────────────────────
# 발주계획 수집 (전체 수집 후 로컬 키워드 필터링)
# ─────────────────────────────────────────────────────────────

def collect_order_plans(date_str: str, days_back: int = 1, keywords: list = None) -> list:
    """
    전체 발주계획 수집 후 사업명 기준으로 키워드 필터링
    """
    kws = keywords if keywords is not None else KEYWORDS
    results = []
    seen = set()
    prev_day = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=days_back)).strftime('%Y%m%d')
    date_start = prev_day + '0000'
    date_end   = date_str + '2359'

    targets = []
    if COLLECT_SERVC:  targets.append(('plan_servc',  '용역'))
    if COLLECT_THNG:   targets.append(('plan_thng',   '물품'))
    if COLLECT_CNSTWK: targets.append(('plan_cnstwk', '공사'))

    for endpoint_key, biz_type in targets:
        print(f"  [발주계획/{biz_type}] 전체 수집 중...")
        params = {
            'ServiceKey': SERVICE_KEY,
            'type': 'json',
            'inqryDiv': '1',
            'inqryBgnDt': date_start,
            'inqryEndDt': date_end,
        }
        items = _fetch_all_pages(ENDPOINTS[endpoint_key], params)
        print(f"    → {len(items)}건 수집, 키워드 필터링 중...")

        for item in items:
            title = item.get('bizNm', '')
            if not _keyword_match(title, kws):
                continue
            uid = item.get('orderPlanUntyNo', '')
            if uid and uid not in seen:
                seen.add(uid)
                results.append(_normalize_plan(item, biz_type))

    return results


# ─────────────────────────────────────────────────────────────
# 메인 수집 함수
# ─────────────────────────────────────────────────────────────

def collect(date_str: str = None, days_back: int = 1, keywords: list = None) -> list:
    """
    date_str: 'YYYYMMDD' (기본값: 오늘)
    days_back: 수집 시작일 (기본 1일 전, 월요일이면 3 권장)
    keywords: 고객별 키워드 (None이면 config.py KEYWORDS 사용)
    세 API 모두 수집 후 통합 반환
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y%m%d')

    kws = keywords if keywords is not None else KEYWORDS
    start_day = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=days_back)).strftime('%Y%m%d')

    print(f"\n{'='*50}")
    print(f"수집 날짜: {date_str} (기간: {start_day} ~ {date_str})")
    print(f"키워드: {kws}")
    print('='*50)

    all_results = []

    print("\n[1/3] 입찰공고 수집")
    bid = collect_bid_notices(date_str, days_back, kws)
    print(f"  → 키워드 매칭 {len(bid)}건")
    all_results.extend(bid)

    print("\n[2/3] 사전규격 수집")
    spec = collect_pre_specs(date_str, days_back, kws)
    print(f"  → 키워드 매칭 {len(spec)}건")
    all_results.extend(spec)

    print("\n[3/3] 발주계획 수집")
    plan = collect_order_plans(date_str, days_back, kws)
    print(f"  → 키워드 매칭 {len(plan)}건")
    all_results.extend(plan)

    print(f"\n{'='*50}")
    print(f"총 수집: {len(all_results)}건")
    print('='*50)

    return all_results


if __name__ == '__main__':
    import sys
    import json

    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    results = collect(date_arg)

    print("\n\n[수집 결과 요약]")
    for i, item in enumerate(results, 1):
        amt = f"{int(item['amount']):,}원" if item['amount'] else '금액 미공개'
        file_cnt = len(item['files'])
        print(f"  [{i}] [{item['source']}] {item['title']}")
        print(f"       기관: {item['agency']} | 금액: {amt} | 첨부: {file_cnt}개")
