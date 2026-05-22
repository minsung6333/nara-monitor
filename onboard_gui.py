# -*- coding: utf-8 -*-
"""
나라장터 모니터 — 고객 등록 GUI
소개서 PDF + 기본 정보 입력 → profile.json 자동 생성 → git push
"""
import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from dotenv import load_dotenv

load_dotenv()

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

REPO_DIR = Path(__file__).parent


# ── 유틸 ──────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_]+", "-", text) or "customer"


PROFILE_SYSTEM = """당신은 기업 분석 전문가입니다.
주어진 회사 소개서 텍스트를 읽고 아래 JSON 형식으로 회사 프로필을 추출하세요.

반환 형식:
{
  "company_name": "회사 정식명칭",
  "description": "한두 줄 핵심 사업 설명",
  "business_areas": ["사업 영역 1", "사업 영역 2"],
  "core_technologies": ["핵심 기술 1", "핵심 기술 2"],
  "certifications": ["인증 1"],
  "target_sectors": ["주요 고객군 1"],
  "exclusions": ["관심 없는 영역 1"],
  "notes": "수주 전략상 특이사항"
}
규칙: 없는 항목은 빈 배열 []. 반드시 위 JSON만 반환."""


def _extract_profile(pdf_path: str, log) -> dict:
    try:
        import fitz
    except ImportError:
        log("  [오류] PyMuPDF 미설치")
        return {}
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    doc = fitz.open(pdf_path)
    text = "\n\n".join(doc[i].get_text() for i in range(min(len(doc), 30)))[:60000]
    doc.close()
    log(f"  PDF {len(doc)}페이지 읽기 완료 → LLM 분석 중...")

    try:
        resp = client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": PROFILE_SYSTEM},
                {"role": "user",   "content": text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=120,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log(f"  [LLM 오류] {e}")
        return {}


def _git(cmd: list[str], log) -> bool:
    try:
        r = subprocess.run(
            ["git"] + cmd, cwd=str(REPO_DIR),
            capture_output=True, text=True, encoding="utf-8"
        )
        if r.stdout.strip():
            log(f"  {r.stdout.strip()}")
        if r.returncode != 0:
            log(f"  [git 오류] {r.stderr.strip()}")
            return False
        return True
    except Exception as e:
        log(f"  [git 실패] {e}")
        return False


# ── GUI ───────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Nara Monitor — 고객 등록")
        self.geometry("700x680")
        self.resizable(False, False)
        self._pdf_path = ""
        self._build_ui()

    def _build_ui(self):
        # ── 헤더 ──────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="#2d3561", corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(header, text="Nara Monitor  ·  고객 등록",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="white").pack(pady=16, padx=24, anchor="w")

        # ── 폼 ────────────────────────────────────────────────
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=28, pady=(18, 0))

        def row(parent, label, row_n):
            ctk.CTkLabel(parent, text=label, anchor="w",
                         font=ctk.CTkFont(size=13)).grid(
                row=row_n, column=0, sticky="w", pady=(0, 4))

        form.columnconfigure(1, weight=1)

        # 회사명
        row(form, "회사명 *", 0)
        self.e_company = ctk.CTkEntry(form, placeholder_text="주식회사 예시")
        self.e_company.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        self.e_company.bind("<FocusOut>", self._auto_id)

        # 고객 ID
        row(form, "고객 ID *", 1)
        id_frame = ctk.CTkFrame(form, fg_color="transparent")
        id_frame.grid(row=1, column=1, sticky="ew", pady=(0, 10))
        id_frame.columnconfigure(0, weight=1)
        self.e_id = ctk.CTkEntry(id_frame, placeholder_text="영문 소문자·하이픈 (폴더명)")
        self.e_id.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(id_frame, text="customers/", text_color="gray",
                     font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="w")

        # 키워드
        row(form, "검색 키워드 *", 2)
        self.e_keywords = ctk.CTkEntry(form, placeholder_text="AI,인공지능,데이터,소프트웨어")
        self.e_keywords.grid(row=2, column=1, sticky="ew", pady=(0, 10))
        self.e_keywords.insert(0, "AI,인공지능,데이터,소프트웨어")

        # 수신 이메일
        row(form, "수신 이메일 *", 3)
        self.e_mail = ctk.CTkEntry(form, placeholder_text="a@company.com,b@company.com")
        self.e_mail.grid(row=3, column=1, sticky="ew", pady=(0, 10))

        # 소개서 PDF
        row(form, "소개서 PDF", 4)
        pdf_frame = ctk.CTkFrame(form, fg_color="transparent")
        pdf_frame.grid(row=4, column=1, sticky="ew", pady=(0, 4))
        pdf_frame.columnconfigure(0, weight=1)
        self.lbl_pdf = ctk.CTkLabel(pdf_frame, text="선택 안 함  (없으면 템플릿 생성)",
                                    text_color="gray", anchor="w",
                                    font=ctk.CTkFont(size=12))
        self.lbl_pdf.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(pdf_frame, text="파일 선택", width=90,
                      command=self._pick_pdf).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(form, text="※ PDF가 없으면 profile.json 템플릿이 생성됩니다. 나중에 직접 편집하세요.",
                     text_color="gray", font=ctk.CTkFont(size=11), anchor="w"
                     ).grid(row=5, column=1, sticky="w", pady=(0, 8))

        # ── 등록 버튼 ─────────────────────────────────────────
        self.btn = ctk.CTkButton(self, text="  등록 및 Git Push  ",
                                 font=ctk.CTkFont(size=14, weight="bold"),
                                 height=42, command=self._start)
        self.btn.pack(pady=(14, 8), padx=28, fill="x")

        # ── 로그 ──────────────────────────────────────────────
        ctk.CTkLabel(self, text="진행 로그", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(padx=28, anchor="w")
        self.log_box = ctk.CTkTextbox(self, height=200, font=ctk.CTkFont(family="Consolas", size=11))
        self.log_box.pack(fill="both", expand=True, padx=28, pady=(4, 20))
        self.log_box.configure(state="disabled")

    # ── 이벤트 ────────────────────────────────────────────────

    def _auto_id(self, _=None):
        if not self.e_id.get():
            self.e_id.insert(0, _slugify(self.e_company.get()))

    def _pick_pdf(self):
        path = filedialog.askopenfilename(
            title="소개서 PDF 선택",
            filetypes=[("PDF 파일", "*.pdf"), ("모든 파일", "*.*")]
        )
        if path:
            self._pdf_path = path
            self.lbl_pdf.configure(text=Path(path).name, text_color="#2d3561")

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.update_idletasks()

    def _start(self):
        company  = self.e_company.get().strip()
        cid      = self.e_id.get().strip() or _slugify(company)
        keywords = [k.strip() for k in self.e_keywords.get().split(",") if k.strip()]
        mail_to  = self.e_mail.get().strip()

        if not company:
            self._log("⚠ 회사명을 입력하세요.")
            return
        if not mail_to:
            self._log("⚠ 수신 이메일을 입력하세요.")
            return
        if not keywords:
            self._log("⚠ 키워드를 입력하세요.")
            return

        self.btn.configure(state="disabled", text="처리 중...")
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        threading.Thread(
            target=self._run,
            args=(company, cid, keywords, mail_to, self._pdf_path),
            daemon=True
        ).start()

    def _run(self, company, cid, keywords, mail_to, pdf_path):
        try:
            self._log(f"[1/4] 폴더 생성: customers/{cid}/")
            dest = REPO_DIR / "customers" / cid
            if dest.exists():
                self._log(f"  이미 존재합니다 — 덮어씁니다.")
            dest.mkdir(parents=True, exist_ok=True)

            # config.json
            config = {"company_name": company, "keywords": keywords,
                      "mail_to": mail_to, "active": True}
            (dest / "config.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log(f"  config.json 저장 완료")

            # profile.json
            self._log("\n[2/4] profile.json 생성")
            if pdf_path and Path(pdf_path).exists():
                profile = _extract_profile(pdf_path, self._log)
                if profile:
                    profile.setdefault("company_name", company)
                    self._log("  프로필 자동 추출 완료")
                else:
                    self._log("  추출 실패 — 템플릿으로 대체")
                    profile = self._blank_profile(company)
            else:
                self._log("  PDF 없음 — 템플릿 생성 (나중에 직접 편집)")
                profile = self._blank_profile(company)

            (dest / "profile.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log("  profile.json 저장 완료")

            # git
            self._log("\n[3/4] Git 커밋")
            _git(["add", f"customers/{cid}/"], self._log)
            msg = f"feat: 고객 등록 — {company} [{cid}]"
            ok = _git(["commit", "-m", msg], self._log)
            if not ok:
                self._log("  커밋할 변경사항이 없거나 실패했습니다.")

            self._log("\n[4/4] Git Push")
            if _git(["push"], self._log):
                self._log("\n✅ 등록 완료! 내일 오전 9시부터 자동 발송됩니다.")
            else:
                self._log("\n⚠ Push 실패. git 인증 설정을 확인하세요.")

        except Exception as e:
            self._log(f"\n[오류] {e}")
        finally:
            self.btn.configure(state="normal", text="  등록 및 Git Push  ")

    @staticmethod
    def _blank_profile(company: str) -> dict:
        return {"company_name": company, "description": "",
                "business_areas": [], "core_technologies": [],
                "certifications": [], "target_sectors": [],
                "exclusions": [], "notes": ""}


if __name__ == "__main__":
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    app = App()
    app.mainloop()
