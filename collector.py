import os
import requests
from urllib.parse import urlencode
from datetime import datetime, timedelta
from config import SERVICE_KEY, KEYWORDS, ENDPOINTS, COLLECT_SERVC, COLLECT_THNG, COLLECT_CNSTWK

ROWS_PER_PAGE = 100

# 나라장터 수집 API(apis.data.go.kr)도 해외 IP를 차단하므로,
# GitHub Actions(해외 IP)에서는 한국 IP 프록시(Vercel icn1)를 경유한다.
# PROXY_URL 설정 시 프록시 경유, 없으면 직접 호출(로컬·한국 IP).
PROXY_URL   = os.getenv('PROXY_URL', '')
PROXY_TOKEN = os.getenv('PROXY_TOKEN', '')


def _api_get(url: str, params: dict, timeout: int = 15) -> requests.Response:
    """API GET 호출. PROXY_URL 있으면 한국 IP 프록시 경유."""
    if PROXY_URL and PROXY_TOKEN:
        full = url + '?' + urlencode(params)
        return requests.get(
            PROXY_URL,
            params={'url': full, 'token': PROXY_TOKEN},
            timeout=timeout,
        )
    return requests.get(url, params=params, timeout=timeout)


def _fetch_all_pages(url: str, base_params: dict) -> list:
    """페이지네이션 처리해서 전체 결과 반환"""
    all_items = []
    page = 1

    while True:
        params = {**base_params, 'pageNo': str(page), 'numOfRows': str(ROWS_PER_PAGE)}
        try:
            res = _api_get(url, params)
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


_PRCM_BSNE_SE_CD = {'용역': '03', '물품': '01', '공사': '02'}

def _normalize_spec(item: dict, biz_type: str) -> dict:
    """사전규격 항목을 공통 형식으로 변환"""
    spec_no = item.get('bfSpecRgstNo', '')
    code = _PRCM_BSNE_SE_CD.get(biz_type, '03')
    detail_url = f'https://www.g2b.go.kr/link/PRVA004_02/single/?bfSpecRegNo={spec_no}&prcmBsneSeCd={code}' if spec_no else ''

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
        'detail_url': detail_url,
        'notice_no': spec_no,
        'files': _extract_files_spec(item),
    }


def _normalize_plan(item: dict, biz_type: str) -> dict:
    """발주계획 항목을 공통 형식으로 변환"""
    # 연결된 입찰공고번호 목록 파싱 (예: "R26BK01539068000,R26BK01539069000")
    bid_list_raw = item.get('bidNtceNoList', '') or ''
    linked_bid_nos = [b.strip() for b in bid_list_raw.split(',') if b.strip()]

    order_no = item.get('orderPlanUntyNo', '')
    code = _PRCM_BSNE_SE_CD.get(biz_type, '03')
    detail_url = f'https://www.g2b.go.kr/link/PRPA015_01/single/?oderPlanNo={order_no}&prcmBsneSeCd={code}' if order_no else ''

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
        'detail_url': detail_url,
        'notice_no': order_no,
        'linked_bid_nos': linked_bid_nos,
        'files': [],
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
# 기관 트래킹 수집 (서버 필터 사용, 키워드 무관)
# ─────────────────────────────────────────────────────────────

# 검증된 서버 필터 파라미터 매핑
_AGENCY_FILTER_PARAM = {
    'bid_servc':  'ntceInsttNm',   # 입찰공고: 공고기관명
    'bid_thng':   'ntceInsttNm',
    'bid_cnstwk': 'ntceInsttNm',
    'spec_servc': 'dminsttNm',     # 사전규격: 수요기관명
    'spec_thng':  'dminsttNm',
    'spec_cnstwk':'dminsttNm',
    'plan_servc': 'orderInsttNm',  # 발주계획: 발주기관명
    'plan_thng':  'orderInsttNm',
    'plan_cnstwk':'orderInsttNm',
}


def collect_by_agencies(
    date_str: str,
    days_back: int = 1,
    agencies: list = None,
    keywords: list = None,
) -> list:
    """
    트래킹 기관별 수집. 동일 기준일자 범위 적용.
    keywords가 주어지면 공고 제목/품명에 키워드 포함된 것만 반환 (2차 필터).
    """
    if not agencies:
        return []

    results = []
    seen = set()  # uid 중복 제거

    prev_day = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=days_back)).strftime('%Y%m%d')
    date_start = prev_day + '0000'
    date_end   = date_str + '2359'

    # 수집 대상 엔드포인트 (config.py의 COLLECT_* 플래그 적용)
    # (endpoint_key, source, biz_type, normalizer, title_field)
    targets = []
    if COLLECT_SERVC:
        targets.extend([
            ('bid_servc',  '입찰공고', '용역', _normalize_bid,  'bidNtceNm'),
            ('spec_servc', '사전규격', '용역', _normalize_spec, 'prdctClsfcNoNm'),
            ('plan_servc', '발주계획', '용역', _normalize_plan, 'bizNm'),
        ])
    if COLLECT_THNG:
        targets.extend([
            ('bid_thng',  '입찰공고', '물품', _normalize_bid,  'bidNtceNm'),
            ('spec_thng', '사전규격', '물품', _normalize_spec, 'prdctClsfcNoNm'),
            ('plan_thng', '발주계획', '물품', _normalize_plan, 'bizNm'),
        ])
    if COLLECT_CNSTWK:
        targets.extend([
            ('bid_cnstwk',  '입찰공고', '공사', _normalize_bid,  'bidNtceNm'),
            ('spec_cnstwk', '사전규격', '공사', _normalize_spec, 'prdctClsfcNoNm'),
            ('plan_cnstwk', '발주계획', '공사', _normalize_plan, 'bizNm'),
        ])

    for agency in agencies:
        print(f"  [트래킹] '{agency}' 수집 중...")
        for endpoint_key, source, biz_type, normalizer, title_field in targets:
            param_name = _AGENCY_FILTER_PARAM[endpoint_key]
            params = {
                'ServiceKey': SERVICE_KEY,
                'type': 'json',
                'inqryDiv': '1',
                'inqryBgnDt': date_start,
                'inqryEndDt': date_end,
                param_name: agency,
            }
            items = _fetch_all_pages(ENDPOINTS[endpoint_key], params)
            for item in items:
                # 키워드 2차 필터 (있는 경우)
                if keywords:
                    title = item.get(title_field, '') or ''
                    if not _keyword_match(title, keywords):
                        continue

                # uid 추출
                uid = (item.get('bidNtceNo', '') + item.get('bidNtceOrd', '')
                       or item.get('bfSpecRgstNo', '')
                       or item.get('orderPlanUntyNo', ''))
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                notice = normalizer(item, biz_type)
                notice['matched_agency'] = agency
                results.append(notice)

    return results


# ─────────────────────────────────────────────────────────────
# 메인 수집 함수
# ─────────────────────────────────────────────────────────────

def collect(
    date_str: str = None,
    days_back: int = 1,
    keywords: list = None,
    tracked_agencies: list = None,
) -> list:
    """
    date_str: 'YYYYMMDD' (기본값: 오늘)
    days_back: 수집 시작일 (기본 1일 전, 월요일이면 3 권장)
    keywords: 고객별 키워드 (None이면 config.py KEYWORDS 사용)
    tracked_agencies: 트래킹 기관 목록 — 키워드와 무관하게 추가 수집
    세 API 모두 수집 후 통합 반환 (키워드 + 기관 트래킹 중복 제거)
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y%m%d')

    kws = keywords if keywords is not None else KEYWORDS
    start_day = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=days_back)).strftime('%Y%m%d')

    print(f"\n{'='*50}")
    print(f"수집 날짜: {date_str} (기간: {start_day} ~ {date_str})")
    print(f"키워드: {kws}")
    if tracked_agencies:
        print(f"트래킹 기관: {tracked_agencies}")
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

    # 키워드 결과 인덱스 (uid → notice) 미리 구축
    keyword_index = {}
    for n in all_results:
        uid = n.get('notice_no', '')
        if uid:
            keyword_index[uid] = n

    # 기관 트래킹 수집 (있는 경우) — 키워드 매칭된 공고만
    if tracked_agencies:
        print(f"\n[4/4] 기관 트래킹 수집 — {len(tracked_agencies)}개 기관 (키워드 매칭 적용)")
        agency_results = collect_by_agencies(
            date_str, days_back, tracked_agencies, keywords=kws,
        )

        new_count = merged_count = 0
        for n in agency_results:
            uid = n.get('notice_no', '')
            if uid and uid in keyword_index:
                # 키워드 매칭과 중복 — matched_agency 정보를 키워드 결과에 병합
                keyword_index[uid]['matched_agency'] = n.get('matched_agency', '')
                merged_count += 1
            else:
                # 신규 (이론상 키워드 필터를 통과했으므로 키워드 결과에도 있을 가능성 큼)
                all_results.append(n)
                new_count += 1

        print(f"  → 기관 트래킹 키워드 매칭 {len(agency_results)}건 (신규 {new_count}건, 머지 {merged_count}건)")

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
