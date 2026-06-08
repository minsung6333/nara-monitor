# 나라장터 AI 공고 모니터링 — 인수인계 문서

> 이 문서는 신규 세션에서 바로 고도화 작업을 이어받을 수 있도록 작성된 인수인계 문서입니다.

---

## 1. 서비스 개요

나라장터(g2b.go.kr) 공공조달 공고를 매일 자동 수집하고, GPT가 회사 역량 대비 관련성을 분석하여 이메일 리포트로 발송하는 SaaS형 모니터링 서비스.

**현재 운영 현황:**
- GitHub Actions로 매일 KST 06:50 자동 실행 (평일만)
- 고객사: 클라비(clabi), 클라비세일즈(clabisales) 2개
- 발신: `hello@clabi.ai` (NaverWorks SMTP)
- 수신: 클라비 → `dx@clabi.ai`, 클라비세일즈 → `msseo@clabi.ai`
- GitHub repo: `https://github.com/minsung6333/nara-monitor`

---

## 2. 디렉토리 구조

```
nara-dev/
├── main.py              # 엔트리포인트 — 멀티 고객 순회 실행
├── collector.py         # 나라장터 3종 API 수집 (입찰공고·사전규격·발주계획)
├── analyzer.py          # 2단계 LLM 분석 파이프라인
├── mailer.py            # HTML 이메일 생성 + SMTP 발송 + Excel 저장
├── extractor.py         # PDF/첨부파일 텍스트 추출 (PyMuPDF, pdfplumber)
├── downloader.py        # 첨부파일 다운로드
├── config.py            # API 키·키워드·엔드포인트 설정
├── onboard.py           # 신규 고객 등록 CLI 스크립트
├── onboard_gui.py       # 신규 고객 등록 GUI 스크립트
├── requirements.txt     # 의존성
├── .env                 # 로컬 환경변수 (gitignore)
├── customers/
│   ├── clabi/
│   │   ├── config.json  # 키워드, 수신처, 활성여부
│   │   └── profile.json # 회사 프로필 (LLM 분석에 사용)
│   └── clabisales/
│       ├── config.json
│       └── profile.json
└── .github/workflows/
    └── daily.yml        # GitHub Actions 스케줄 (KST 06:50, 평일)
```

---

## 3. 데이터 흐름

```
main.py
  └─ customers/ 폴더 순회
       └─ collector.collect(date_str, keywords)
            ├─ 입찰공고 API (키워드 파라미터로 서버 필터링)
            ├─ 사전규격 API (전체 수집 후 로컬 키워드 필터링)
            └─ 발주계획 API (전체 수집 후 로컬 키워드 필터링)
       └─ analyzer.run_pipeline(notices, profile)
            ├─ [1단계] screen_titles() — 전체 공고 제목 배치 스크리닝
            │    └─ GPT 1회 호출 → HIGH / MED / LOW 분류
            └─ [2단계] analyze_notice() × HIGH+MED 건수 (병렬 5개)
                 ├─ PDF 첨부파일 다운로드
                 ├─ extractor로 사업 개요 추출
                 └─ GPT 상세 분석 → score(0~100), verdict, 강점, 리스크, 권장행동
       └─ mailer.send_report()
            ├─ HTML 이메일 생성 (즉시검토🔴 / 검토고려🟡 / 전체목록)
            ├─ Excel 파일 생성 (분석결과 + 전체수집 시트)
            └─ SMTP 발송
```

---

## 4. 핵심 파일 상세

### config.json (고객별)
```json
{
  "company_name": "회사명",
  "keywords": ["AI", "인공지능", "데이터", "소프트웨어"],
  "mail_to": "recipient@company.com",
  "active": true
}
```

### profile.json (고객별)
LLM이 분석 시 참조하는 회사 역량 정보:
- `business_areas`: 사업 영역 목록
- `core_technologies`: 핵심 기술 목록
- `target_sectors`: 주요 고객 섹터
- `references`: 주요 레퍼런스 사업
- `exclusions`: 관심 없는 영역 (LOW 판정 기준)
- `min_amount`: 최소 금액 기준
- `notes`: 특이사항

### analyzer.py 모델 설정
```python
MODEL_SCREEN  = "gpt-4.1"   # 1단계 배치 스크리닝
MODEL_ANALYZE = "gpt-4.1"   # 2단계 상세 분석
MAX_WORKERS   = 5            # 병렬 처리 수
```

### .env (로컬 / GitHub Secrets 동일 구조)
```
OPENAI_API_KEY=...
NARA_SERVICE_KEY=...       # 공공데이터포털 API 키 (config.py에 하드코딩 fallback 있음)
SMTP_HOST=smtp.worksmobile.com
SMTP_PORT=465
SMTP_USER=hello@clabi.ai
SMTP_PASS=...
MAIL_FROM=hello@clabi.ai
MAIL_FROM_NAME=나라장터 AI 분석 시스템
MAIL_TO=...                # fallback용, 실제 수신처는 customers/*/config.json
```

---

## 5. GitHub Actions

```yaml
# .github/workflows/daily.yml
on:
  schedule:
    - cron: '50 21 * * 0-4'   # UTC 21:50 = KST 06:50, 일~목(UTC) = 월~금(KST)
  workflow_dispatch:            # 수동 실행 가능
```

GitHub Secrets에 등록된 항목:
`OPENAI_API_KEY`, `NARA_SERVICE_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_FROM_NAME`, `MAIL_TO`

---

## 6. 로컬 실행 방법

```bash
# 환경 셋업
pip install -r requirements.txt

# 실행 (오늘 날짜 기준)
python main.py

# 특정 날짜 실행
python main.py 20260601

# 단일 모듈 테스트
python collector.py 20260601
python analyzer.py
```

---

## 7. 고도화 논의 배경

현재 서비스는 동작하지만 아래 방향으로 고도화 예정:

### 현재 한계점
- 키워드 기반 수집이라 관련 공고 누락 가능성 있음
- 1단계 스크리닝에서 전체 공고를 LLM에 한 번에 전달 → 공고 수 많을 때 토큰 한계
- 고객별 분석 결과가 로컬 파일로만 저장 (DB 없음)
- 신규 고객 추가 시 profile.json 수동 작성 필요
- 발주계획 → 입찰공고 연결 추적 미구현

### 고도화 방향 (오너 의도)
- 멀티 고객 SaaS 구조 확장
- 분석 품질 개선 (RFP 첨부파일 분석 강화)
- 웹 대시보드 또는 관리 UI
- 고객별 분석 이력 저장 및 조회
- 알림 채널 확장 (Slack, 카카오 등)

---

## 8. 주의사항

- `.env` 파일은 gitignore되어 있음. GitHub Secrets에 별도 등록 필요
- `config.py`의 `SERVICE_KEY`는 fallback 하드코딩 있으나 GitHub Actions에서는 Secrets 사용
- `customers/clabisales/profile.json`은 클라비 영업팀 관점 특화 프로필 (clabi와 다름)
- 월요일 실행 시 금요일 공고 수집 (UTC 기준 일요일 = KST 기준 월요일 로직)
- NaverWorks SMTP는 port 465 (SSL), starttls 아님
