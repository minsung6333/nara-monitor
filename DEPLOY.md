# Streamlit Cloud 배포 가이드

대시보드(`dashboard.py`)를 Streamlit Community Cloud에 무료로 배포하는 절차입니다.

---

## 1단계: GitHub Personal Access Token 발급

대시보드가 변경사항을 자동으로 git push 하려면 토큰이 필요합니다.

1. https://github.com/settings/tokens?type=beta 접속
2. **Generate new token** → Fine-grained personal access token
3. 설정:
   - **Token name**: `nara-dashboard-deploy`
   - **Expiration**: 1년 또는 365 days
   - **Repository access**: Only select repositories → `minsung6333/nara-monitor` 선택
   - **Repository permissions**:
     - `Contents` → **Read and write**
     - `Actions` → **Read and write** (지금 실행 버튼용)
4. Generate → `ghp_xxx` 형태 토큰 복사 (한 번만 보임)

---

## 2단계: Streamlit Cloud 계정 + 배포

1. https://share.streamlit.io 접속 → GitHub 계정으로 로그인
2. **New app** 클릭
3. 설정:
   - **Repository**: `minsung6333/nara-monitor`
   - **Branch**: `main`
   - **Main file path**: `dashboard.py`
   - **App URL**: 원하는 서브도메인 (예: `nara-admin`) → `https://nara-admin.streamlit.app`
4. **Advanced settings** → Python version: 3.11
5. **Deploy** 클릭

배포 진행 중 빌드 로그가 뜹니다 (의존성 설치). 3~5분 소요.

---

## 3단계: Secrets 등록

App이 떴는데 비밀번호 게이트에서 "DASHBOARD_PASSWORD가 설정되지 않았습니다" 에러가 나옵니다. 이건 정상.

1. 우측 하단 ⋯ → **Settings** → **Secrets**
2. 다음 TOML 그대로 붙여넣기 (값은 실제 값으로 교체):

```toml
DASHBOARD_PASSWORD = "강력한_랜덤_비밀번호"

OPENAI_API_KEY   = "sk-..."
NARA_SERVICE_KEY = "..."

GITHUB_TOKEN = "ghp_..."           # 1단계에서 만든 토큰
GITHUB_REPO  = "minsung6333/nara-monitor"

GIT_USER_NAME  = "Nara Dashboard Bot"
GIT_USER_EMAIL = "bot@clabi.ai"
```

3. **Save** → 앱이 자동 재시작됨

---

## 4단계: Private 모드 (선택, 강력 권장)

비밀번호만으로도 1차 방어는 되지만, Streamlit Cloud 자체 인증도 켜면 이중 방어입니다.

1. App Settings → **Sharing**
2. **Who can view this app?** → **Only specific people**
3. 접근 허용할 Google 이메일 추가 (사내 사용자 이메일)
4. 저장

이제 해당 이메일로 Google 로그인한 사람만 접근 가능 + 그 후 비밀번호 게이트 추가 통과 필요.

---

## 5단계: 동작 확인 체크리스트

- [ ] 배포된 URL 접속 → 로그인 화면 표시
- [ ] DASHBOARD_PASSWORD로 로그인 → 대시보드 진입
- [ ] 고객사 목록이 정상 로드
- [ ] 키워드/이메일 변경 후 저장 → GitHub 저장소 commit 확인
- [ ] "지금 실행" 버튼 → GitHub Actions 트리거 → 메일 도착

---

## 트러블슈팅

### "DASHBOARD_PASSWORD가 설정되지 않았습니다"
→ Secrets 등록 후 앱이 재시작될 때까지 1~2분 대기. 안 되면 App settings → Reboot.

### "git push 실패"
→ GITHUB_TOKEN 권한 부족. Contents:write가 켜졌는지 확인.

### "OpenAI API key 오류"
→ OPENAI_API_KEY가 비어있거나 만료. Secrets에서 재등록.

### Streamlit Cloud 60분 idle 후 sleep
→ 무료 플랜 특성. 누가 접속하면 자동 재시작됨 (10~20초 소요).

---

## 보안 체크포인트

- ✅ `.env` `.streamlit/secrets.toml` 모두 .gitignore에 포함 (이 저장소에 없음)
- ✅ 토큰은 특정 저장소만 접근 가능 (fine-grained)
- ✅ 비밀번호 게이트 + (선택) Streamlit Sharing 화이트리스트
- ⚠️ 토큰 노출 시 즉시 GitHub에서 revoke
