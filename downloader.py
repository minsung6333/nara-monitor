import os
import re
import tempfile
import requests
from urllib.parse import unquote
from pathlib import Path

# HWP 계열보다 PDF를 우선시하는 확장자 우선순위 (낮을수록 우선)
_EXT_PRIORITY = {
    '.pdf': 0,
    '.hwpx': 1,
    '.hwp': 2,
}
_SKIP_EXTS = {'.hwp', '.hwpx'}


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


def select_files(files: list[dict]) -> list[dict]:
    """
    files: [{'name': str, 'url': str}, ...]
    같은 base name이면 PDF를 우선 선택, HWP/HWPX만 있으면 그대로 사용.
    반환: 다운로드할 파일 목록 (중복 제거 후)
    """
    # base name → 최우선 파일 dict 매핑
    best: dict[str, dict] = {}
    for f in files:
        if not f.get('url'):
            continue
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


def download_files(files: list[dict], dest_dir: str = None) -> list[dict]:
    """
    files: select_files() 결과 또는 raw files 리스트
    dest_dir: 저장 경로 (None이면 임시 디렉터리 생성)
    반환: [{'name': str, 'url': str, 'local_path': str, 'ext': str}, ...]
    PDF가 아닌 파일(HWP 등)도 경로는 반환하되 ext 필드로 구분 가능
    """
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix='nara_')
    os.makedirs(dest_dir, exist_ok=True)

    results = []
    selected = select_files(files)

    for f in selected:
        url = f['url']
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [다운로드 실패] {url}: {e}")
            continue

        filename = _get_filename_from_response(resp, url)
        # 같은 이름 파일이 이미 있으면 번호 붙임
        local_path = os.path.join(dest_dir, filename)
        if os.path.exists(local_path):
            stem, suffix = os.path.splitext(filename)
            local_path = os.path.join(dest_dir, f"{stem}_1{suffix}")

        with open(local_path, 'wb') as fp:
            fp.write(resp.content)

        ext = Path(filename).suffix.lower()
        results.append({
            'name': filename,
            'url': url,
            'local_path': local_path,
            'ext': ext,
        })
        print(f"  [다운로드] {filename} ({len(resp.content):,} bytes)")

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
