"""공지 게시판을 확인해서, 키워드가 포함된 '새 글'만 이메일로 보내는 스크립트.

설정은 모두 환경변수로 받습니다 (GitHub Actions의 Variables / Secrets에 등록).
  NOTICE_URLS         감시할 공지 목록 페이지 주소 (여러 개면 줄바꿈 또는 쉼표로 구분)
  KEYWORDS            키워드 (여러 개면 쉼표로 구분, 하나라도 포함되면 알림)
  LIST_SELECTOR       글 링크를 고르는 CSS 선택자 (기본값 "a")
  MATCH_BODY          "1"이면 제목뿐 아니라 본문에서도 키워드를 찾음 (기본 "0")
  GMAIL_USER          보내는 Gmail 주소
  GMAIL_APP_PASSWORD  Gmail 앱 비밀번호 (일반 비밀번호 아님)
  MAIL_TO             받는 주소 (비우면 GMAIL_USER 본인에게 발송)
  DEBUG               "1"이면 찾은 링크 목록만 출력하고 종료 (선택자 점검용)
  TEST_MAIL           "1"이면 테스트 메일 한 통만 보내고 종료
"""
import json
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("seen.json")
MAX_SEEN = 2000
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def env_list(name):
    raw = os.environ.get(name, "")
    return [x.strip() for x in re.split(r"[,\n]", raw) if x.strip()]


def get_html(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def parse_posts(html, base_url, selector):
    """{글 주소: 제목} 반환"""
    soup = BeautifulSoup(html, "html.parser")
    posts = {}
    for a in soup.select(selector):
        href = (a.get("href") or "").strip()
        title = " ".join(a.get_text().split())
        if not href or href.startswith(("#", "javascript:", "mailto:")) or not title:
            continue
        posts[urljoin(base_url, href)] = title
    return posts


def body_text(url):
    try:
        soup = BeautifulSoup(get_html(url), "html.parser")
        return " ".join(soup.get_text().split())
    except Exception as e:  # 본문을 못 읽어도 전체 작업은 계속
        print(f"[warn] 본문 확인 실패 {url}: {e}")
        return ""


def matches(title, link, keywords, match_body):
    low = title.lower()
    if any(k.lower() in low for k in keywords):
        return True
    if match_body:
        body = body_text(link).lower()
        return any(k.lower() in body for k in keywords)
    return False


def send_mail(subject, body):
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("MAIL_TO") or user
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, password)
        s.sendmail(user, [to], msg.as_string())


def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return None  # 첫 실행 표시


def save_seen(seen_list):
    STATE_FILE.write_text(
        json.dumps(seen_list[-MAX_SEEN:], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def main():
    if os.environ.get("TEST_MAIL") == "1":
        send_mail("[공지알림] 테스트 메일", "이 메일이 보이면 메일 설정은 정상입니다.")
        print("테스트 메일 발송 완료")
        return

    urls = env_list("NOTICE_URLS")
    keywords = env_list("KEYWORDS")
    selector = os.environ.get("LIST_SELECTOR", "").strip() or "a"
    match_body = os.environ.get("MATCH_BODY") == "1"
    debug = os.environ.get("DEBUG") == "1"

    if not urls or (not keywords and not debug):
        sys.exit("NOTICE_URLS와 KEYWORDS를 설정해 주세요.")

    all_posts = {}
    for url in urls:
        try:
            posts = parse_posts(get_html(url), url, selector)
        except Exception as e:
            print(f"[error] {url} 가져오기 실패: {e}")
            continue
        print(f"{url} -> 링크 {len(posts)}개 발견")
        all_posts.update(posts)

    if debug:
        for link, title in all_posts.items():
            print(f"- {title}\n    {link}")
        return

    if not all_posts:
        sys.exit("글을 하나도 못 찾았습니다. LIST_SELECTOR를 확인하세요 (DEBUG=1로 점검 가능).")

    seen = load_seen()
    first_run = seen is None
    seen = seen or set()
    new_posts = {l: t for l, t in all_posts.items() if l not in seen}
    print(f"새 글 {len(new_posts)}개 (첫 실행: {first_run})")

    # 첫 실행은 기존 글을 '이미 본 글'로만 기록하고 메일은 보내지 않음 (메일 폭탄 방지)
    hits = []
    if not first_run:
        hits = [(t, l) for l, t in new_posts.items() if matches(t, l, keywords, match_body)]

    if hits:
        lines = [f"- {t}\n  {l}" for t, l in hits]
        subject = f"[공지알림] 키워드 포함 새 글 {len(hits)}건: {hits[0][0][:40]}"
        send_mail(subject, "\n\n".join(lines))
        print(f"메일 발송: {len(hits)}건")

    # 메일 발송이 성공한 뒤에만 기록 (실패하면 다음 실행에서 다시 시도)
    save_seen(list(seen) + list(new_posts.keys()))


if __name__ == "__main__":
    main()
