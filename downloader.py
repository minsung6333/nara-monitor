import os
import re
import tempfile
import requests
from urllib.parse import unquote
from pathlib import Path

# 나라장터(www.g2b.go.kr)는 해외 IP 다운로드를 차단함.
# GitHub Actions(해외 IP)에서는 한국 IP 프록시(Vercel icn1)를 경유해야 한다.
# PROXY_URL이 설정돼 있으면 프록시 경유, 없으면 직접 다운로드(로컬·한국 IP).
PROXY_URL   = os.getenv('PROXY_URL', '')     # 예: https://nara-proxy.vercel.app/api/download
PROXY_TOKEN = os.getenv('PROXY_TOKEN', '')
PROXY_CHUNK = 4 * 1024 * 1024                # 프록시 응답 4MB 제한 → 청크 크기

# 같은 사업의 문서가 여러 포맷으로 올라온 경우 우선순위 (낮을수록 우선)
# PDF가 가장 깔끔하지만, HWP/HWPX도 직접 파싱(extractor)으로 추출 가능
_EXT_PRIORITY = {
    '.pdf': 0,
    '.hwpx': 1,
    '.hwp': 2,
}

# RFP 문서로 인식할 파일명 키워드 (이 중 하나라도 포함되어야 검토 대상)
RFP_FILENAME_KEYWORDS = ('제안요청서', '과업지시서', '과업내용서')

# 텍스트 추출 가능한 확장자 (analyzer가 이 확장자만 분석 대상으로 사용)
EXTRACTABLE_EXTS = {'.pdf', '.hwp', '.hwpx'}


def _get_filename_from_response(resp: requests.Response, url: str) -> str:
    """Content-Disposition 헤더에서 파일명 추출, 없으면 URL에서 추출"""
    cd = resp.headers.get('Content-Disposition', '')
    if cd:
        # filename*=UTF-8''... 형식
        m = re.search(r"filename\*=(?:UTF-8'')?([^\s;]+)", cd, re.IGNORECASE)
        if m:
            return unquote(m.group(1))
        # filename="..." 형식
        m = re.search(r'filename="?([^";\r\n]+)"?', cd, re.IGNORECASE)
        if m:
            return unquote(m.group(1).strip())
    # URL에서 마지막 경로 세그먼트 사용
    return unquote(url.split('/')[-1].split('?')[0]) or 'file'


def _base_name(filename: str) -> str:
    """확장자를 제외한 base 이름 반환 (소문자)"""
    return Path(filename).stem.lower()


def _is_rfp_file(name: str) -> bool:
    """파일명에 RFP 키워드(제안요청서/과업지시서)가 포함되는지"""
    return any(kw in name for kw in RFP_FILENAME_KEYWORDS)


# 파일명을 알 수 없어 collector가 임시로 붙인 placeholder 패턴
# (사전규격 API는 파일명을 안 주므로 '규격문서N'으로 표기됨)
_PLACEHOLDER_PAT = re.compile(r'^(규격문서|첨부파일)\d+$')


def _is_placeholder_name(name: str) -> bool:
    return bool(_PLACEHOLDER_PAT.match(name.strip()))


def select_files(files: list[dict]) -> list[dict]:
    """
    files: [{'name': str, 'url': str}, ...]
    1차 필터: 파일명에 RFP 키워드(제안요청서/과업지시서)가 있거나,
              파일명을 모르는 placeholder(사전규격 '규격문서N')는 일단 통과
              → 실제 RFP 여부는 다운로드 후 Content-Disposition 파일명으로 재판별
    2차: 같은 base name이면 PDF 우선 선택
    반환: 다운로드 후보 목록 (없으면 빈 리스트 → PDF 검토 스킵)
    """
    candidates = [
        f for f in files
        if f.get('url') and (_is_rfp_file(f.get('name', '')) or _is_placeholder_name(f.get('name', '')))
    ]
    if not candidates:
        return []

    # base name → 최우선 파일 dict 매핑 (PDF 우선)
    best: dict[str, dict] = {}
    for f in candidates:
        name = f.get('name', '') or Path(f['url'].split('?')[0]).name
        ext = Path(name).suffix.lower()
        base = _base_name(name)
        priority = _EXT_PRIORITY.get(ext, 99)

        if base not in best:
            best[base] = (priority, f)
        else:
            cur_priority, _ = best[base]
            if priority < cur_priority:
                best[base] = (priority, f)

    return [f for _, f in best.values()]


def _fetch_direct(url: str) -> tuple[bytes, str]:
    """g2b에서 직접 다운로드 (한국 IP 환경). (내용, 파일명) 반환."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content, _get_filename_from_response(resp, url)


def _fetch_via_proxy(url: str) -> tuple[bytes, str]:
    """
    한국 IP 프록시(Vercel icn1) 경유 다운로드. (내용, 파일명) 반환.
    4MB 초과 파일은 start/end 청크로 나눠 받아 이어붙인다.
    """
    params = {'url': url, 'token': PROXY_TOKEN}
    r = requests.get(PROXY_URL, params=params, timeout=60)

    # 4MB 이하: 한 번에 수신
    if r.status_code == 200:
        filename = _get_filename_from_response(r, url)
        return r.content, filename

    # 4MB 초과: 청크 분할 (413 + X-Total-Length)
    if r.status_code == 413:
        total = int(r.headers.get('X-Total-Length', '0'))
        if total <= 0:
            raise RuntimeError('프록시 413인데 X-Total-Length 없음')
        buf = bytearray()
        filename = ''
        for start in range(0, total, PROXY_CHUNK):
            end = min(start + PROXY_CHUNK, total)
            cr = requests.get(
                PROXY_URL,
                params={'url': url, 'token': PROXY_TOKEN, 'start': start, 'end': end},
                timeout=60,
            )
            cr.raise_for_status()
            buf.extend(cr.content)
            if not filename:
                filename = _get_filename_from_response(cr, url)
        return bytes(buf), filename

    r.raise_for_status()
    raise RuntimeError(f'프록시 예상치 못한 응답: {r.status_code}')


def download_files(files: list[dict], dest_dir: str = None) -> list[dict]:
    """
    files: select_files() 결과 또는 raw files 리스트
    dest_dir: 저장 경로 (None이면 임시 디렉터리 생성)
    반환: [{'name': str, 'url': str, 'local_path': str, 'ext': str}, ...]
    PDF가 아닌 파일(HWP 등)도 경로는 반환하되 ext 필드로 구분 가능

    PROXY_URL 설정 시 한국 IP 프록시 경유, 미설정 시 직접 다운로드.
    """
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix='nara_')
    os.makedirs(dest_dir, exist_ok=True)

    use_proxy = bool(PROXY_URL and PROXY_TOKEN)
    results = []
    selected = select_files(files)

    for f in selected:
        url = f['url']
        try:
            if use_proxy:
                content, filename = _fetch_via_proxy(url)
            else:
                content, filename = _fetch_direct(url)
        except Exception as e:
            print(f"  [다운로드 실패] {url}: {e}")
            continue

        # 같은 이름 파일이 이미 있으면 번호 붙임
        local_path = os.path.join(dest_dir, filename)
        if os.path.exists(local_path):
            stem, suffix = os.path.splitext(filename)
            local_path = os.path.join(dest_dir, f"{stem}_1{suffix}")

        with open(local_path, 'wb') as fp:
            fp.write(content)

        ext = Path(filename).suffix.lower()
        print(f"  [다운로드] {filename} ({len(content):,} bytes)")

        # placeholder(사전규격 '규격문서N')로 통과한 파일은
        # 실제 파일명(Content-Disposition)으로 RFP 키워드 재판별
        orig_name = f.get('name', '')
        if _is_placeholder_name(orig_name) and not _is_rfp_file(filename):
            print(f"    → RFP 아님(제외): {filename}")
            continue

        results.append({
            'name': filename,
            'url': url,
            'local_path': local_path,
            'ext': ext,
        })

    return results


def download_notice_files(notice: dict, dest_dir: str = None) -> list[dict]:
    """
    수집된 공고 1건의 files를 다운로드.
    dest_dir이 None이면 임시 디렉터리 자동 생성.
    """
    files = notice.get('files', [])
    if not files:
        return []
    return download_files(files, dest_dir)


if __name__ == '__main__':
    # 간단 테스트: 하드코딩된 URL로 확인
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    test_files = [
        {'name': '제안요청서.hwp',  'url': 'https://www.g2b.go.kr:8101/ep/preparation/viewBfSpecDocDetail.do?specDocId=test.hwp'},
        {'name': '제안요청서.pdf',  'url': 'https://www.g2b.go.kr:8101/ep/preparation/viewBfSpecDocDetail.do?specDocId=test.pdf'},
        {'name': '과업지시서.hwpx', 'url': 'https://www.g2b.go.kr:8101/ep/preparation/viewBfSpecDocDetail.do?specDocId=task.hwpx'},
    ]

    print("=== select_files 테스트 ===")
    selected = select_files(test_files)
    for f in selected:
        print(f"  선택: {f['name']}")
