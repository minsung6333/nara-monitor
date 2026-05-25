# 나라장터 AI 공고 모니터

나라장터 공공조달 공고를 매일 자동 수집·분석해 이메일로 발송하는 시스템입니다.  
AI가 첨부파일(PDF/HWP)까지 읽고 회사 역량과의 적합도를 평가합니다.

---

## 주요 기능

- **자동 수집**: 입찰공고 / 사전규격 / 발주계획 세 가지 유형 수집
- **2단계 AI 분석**
  - 1단계: 제목·메타 기반 HIGH / MED / LOW 스크리닝
  - 2단계: 첨부파일(PDF/HWP) 다운로드 → 사업 개요 추출 → 심층 적합도 평가
- **HTML 이메일 발송**: 공고 요약 + 분석 결과 + 강점/리스크 + 공고 바로가기 링크
- **엑셀 리포트 첨부**: 분석결과 + 전체수집 시트
- **멀티 고객 지원**: `customers/` 폴더 기반으로 고객별 키워드·수신자 분리 운영
- **GitHub Actions 자동화**: 매일 KST 07:00 실행 (전일 공고 기준)

---

## 디렉터리 구조

```
nara/
├── main.py              # 멀티 고객 실행 엔트리포인트
├── collector.py         # 나라장터 API 수집 (입찰공고/사전규격/발주계획)
├── analyzer.py          # 2단계 AI 분석 파이프라인 (OpenAI)
├── mailer.py            # HTML 이메일 빌드 & 발송 & 엑셀 저장
├── downloader.py        # 첨부파일 다운로드
├── extractor.py         # PDF/HWP 텍스트 추출
├── config.py            # 키워드·API 엔드포인트 설정
├── onboard.py           # 고객 등록 CLI 스크립트
├── onboard_gui.py       # 고객 등록 GUI
├── requirements.txt
├── customers/
│   ├── clabi/
│   │   ├── config.json  # 키워드, 수신자 이메일
│   │   └── profile.json # 회사 프로필 (AI 분석 기준)
│   └── clabisales/
│       ├── config.json
│       └── profile.json
└── .github/
    └── workflows/
        └── daily.yml    # GitHub Actions 스케줄
```

---

## 빠른 시작

### 1. 환경 설정

```bash
pip install -r requirements.txt
```

`.env` 파일 생성:

```env
NARA_SERVICE_KEY=공공데이터포털_서비스키
OPENAI_API_KEY=sk-...
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=sender@example.com
SMTP_PASS=password
MAIL_FROM=sender@example.com
MAIL_FROM_NAME=나라장터 AI 분석 시스템
MAIL_TO=recipient@example.com
```

### 2. 고객 등록

```bash
python onboard.py
```

또는 `customers/{고객ID}/` 폴더에 직접 `config.json`, `profile.json` 생성

**config.json 예시:**
```json
{
  "company_name": "회사명",
  "keywords": ["AI", "인공지능", "데이터"],
  "mail_to": "a@company.com, b@company.com",
  "active": true
}
```

### 3. 수동 실행

```bash
# 오늘 기준 (전일 공고)
python main.py

# 특정 날짜 지정
python main.py 20260522
```

---

## GitHub Actions 자동화

`.github/workflows/daily.yml`에서 매일 **KST 07:00** (UTC 22:00) 자동 실행됩니다.

필요한 GitHub Secrets:

| Secret | 설명 |
|--------|------|
| `NARA_SERVICE_KEY` | 공공데이터포털 API 키 |
| `OPENAI_API_KEY` | OpenAI API 키 |
| `SMTP_HOST` | SMTP 서버 주소 |
| `SMTP_PORT` | SMTP 포트 (465 / 587) |
| `SMTP_USER` | SMTP 계정 |
| `SMTP_PASS` | SMTP 비밀번호 |
| `MAIL_FROM` | 발신자 이메일 |
| `MAIL_FROM_NAME` | 발신자 표시명 |
| `MAIL_TO` | 기본 수신자 (고객별 config.json 우선) |

---

## 이메일 리포트 구성

```
📊 수집 N건 | 🔴 즉시검토 N건 | 🟡 검토고려 N건

🔴 즉시 검토 카드
  ├─ 제목 / 기관 / 금액 / 마감일
  ├─ 📋 공고 요약 (사업 내용 객관적 설명)
  ├─ 🔍 분석 결과 (적합도 관점)
  ├─ ✅ 강점 / ⚠️ 리스크
  └─ ▶ 권장 행동 | 공고 바로가기 →

🟡 검토 고려 카드
  └─ (동일 구성)

📋 전체 수집 목록 (펼치기)
```

---

## 공고 바로가기 URL 패턴

| 유형 | URL |
|------|-----|
| 입찰공고 | API 직접 제공 (`bidNtceDtlUrl`) |
| 사전규격 | `g2b.go.kr/link/PRVA004_02/single/?bfSpecRegNo={no}&prcmBsneSeCd={code}` |
| 발주계획 | `g2b.go.kr/link/PRPA015_01/single/?oderPlanNo={no}&prcmBsneSeCd={code}` |

`prcmBsneSeCd`: 용역=`03`, 물품=`01`, 공사=`02`
