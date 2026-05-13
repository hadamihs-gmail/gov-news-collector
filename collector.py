"""
政府省庁プレスリリース収集スクリプト
対象: 環境省・経済産業省・国土交通省・農林水産省・内閣府
キーワード: 廃棄物処理、リサイクル、脱炭素、CO2排出量算定、SCOPE 等
"""

import os
import smtplib
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dataclasses import dataclass, field
from typing import Optional
import re

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

KEYWORDS = [
    # 廃棄物・リサイクル
    "廃棄物", "廃プラ", "廃プラスチック", "産廃", "一廃", "一般廃棄物",
    "産業廃棄物", "リサイクル", "再資源化", "再利用", "有価物",
    "循環型", "サーキュラー", "3R", "拡大生産者責任", "EPR",
    # 脱炭素・気候変動
    "脱炭素", "カーボンニュートラル", "カーボン", "温室効果ガス",
    "GHG", "ゼロエミッション", "省エネ", "再生可能エネルギー", "再エネ",
    # CO2・排出量
    "CO2", "CO₂", "二酸化炭素", "排出量", "排出権", "カーボンクレジット",
    "Jクレジット", "算定", "インベントリ",
    # SCOPE
    "Scope 1", "Scope 2", "Scope 3", "スコープ",
    # その他関連
    "ESG", "サステナビリティ", "SDGs", "グリーン", "環境配慮",
    "バイオマス", "水素", "アンモニア", "CCS", "CCUS",
    "カーボンフットプリント", "LCA", "ライフサイクル",
]

SOURCES = [
    {
        "name": "環境省",
        "url": "https://www.env.go.jp/press/",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.env.go.jp",
    },
    {
        "name": "経済産業省",
        "url": "https://www.meti.go.jp/press/",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.meti.go.jp",
    },
    {
        "name": "国土交通省",
        "url": "https://www.mlit.go.jp/report/press/index.html",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.mlit.go.jp",
    },
    {
        "name": "農林水産省",
        "url": "https://www.maff.go.jp/j/press/",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.maff.go.jp",
    },
    {
        "name": "内閣府",
        "url": "https://www.cao.go.jp/press/index.html",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.cao.go.jp",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GovNewsCollector/1.0)"
    )
}
REQUEST_TIMEOUT = 20


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class Article:
    ministry: str
    title: str
    url: str
    date_str: str = ""
    matched_keywords: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# スクレイピング
# ---------------------------------------------------------------------------

def fetch_html(url: str, encoding: str = "utf-8") -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = encoding
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None


def keyword_match(text: str) -> list:
    text_lower = text.lower()
    matched = []
    for kw in KEYWORDS:
        if kw.lower() in text_lower:
            matched.append(kw)
    return matched


def extract_date_str(text: str) -> str:
    patterns = [
        r"\d{4}年\d{1,2}月\d{1,2}日",
        r"\d{4}[./]\d{1,2}[./]\d{1,2}",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return ""


def extract_articles(soup: BeautifulSoup, source: dict) -> list:
    articles = []
    seen_urls = set()

    candidates = soup.select(source["item_selector"])
    if not candidates:
        candidates = soup.find_all("a", href=True)

    for item in candidates:
        anchor = item if item.name == "a" else item.find("a", href=True)
        if not anchor:
            continue

        title = anchor.get_text(strip=True)
        href = anchor.get("href", "")
        if not title or not href:
            continue

        if href.startswith("http"):
            full_url = href
        elif href.startswith("/"):
            full_url = source["link_base"] + href
        else:
            full_url = source["link_base"] + "/" + href

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        parent_text = item.get_text(" ", strip=True)
        date_str = extract_date_str(parent_text)
        matched = keyword_match(title + " " + parent_text)

        if matched:
            articles.append(Article(
                ministry=source["name"],
                title=title,
                url=full_url,
                date_str=date_str,
                matched_keywords=list(dict.fromkeys(matched)),
            ))

    return articles


def collect_all() -> list:
    all_articles = []
    for source in SOURCES:
        logger.info(f"Fetching: {source['name']} ({source['url']})")
        soup = fetch_html(source["url"], source.get("encoding", "utf-8"))
        if soup is None:
            continue
        articles = extract_articles(soup, source)
        logger.info(f"  -> {len(articles)} 件マッチ")
        all_articles.extend(articles)
    return all_articles


# ---------------------------------------------------------------------------
# レポート生成
# ---------------------------------------------------------------------------

def build_html_report(articles: list, report_date: str) -> str:
    ministry_groups: dict = {}
    for a in articles:
        ministry_groups.setdefault(a.ministry, []).append(a)

    body_html = ""
    if not articles:
        body_html = '<p style="color:#888;">本日は対象キーワードに合致する情報は見つかりませんでした。</p>'
    else:
        for ministry, items in ministry_groups.items():
            body_html += f'<h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:4px;font-size:16px;margin:24px 0 12px;">{ministry}（{len(items)} 件）</h2>'
            for item in items:
                kw_badges = " ".join(
                    f'<span style="display:inline-block;background:#d5e8d4;color:#274e13;border-radius:3px;'
                    f'padding:2px 7px;font-size:11px;margin:2px 2px 2px 0;">{kw}</span>'
                    for kw in item.matched_keywords[:5]
                )
                body_html += f"""
<div style="background:#f9f9f9;border:1px solid #e0e0e0;border-radius:6px;padding:12px 14px;margin-bottom:10px;">
  <div style="font-size:11px;color:#888;margin-bottom:4px;">{item.date_str or "—"}</div>
  <div style="margin-bottom:6px;"><a href="{item.url}" style="color:#1a5276;font-size:14px;line-height:1.5;">{item.title}</a></div>
  <div>{kw_badges}</div>
</div>"""

    total = len(articles)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>政府省庁 環境・廃棄物関連ニュース {report_date}</title>
</head>
<body style="font-family:'Hiragino Kaku Gothic Pro',Meiryo,sans-serif;max-width:680px;margin:auto;padding:16px;color:#333;font-size:14px;">
  <div style="background:#1a5276;color:#fff;padding:16px;border-radius:6px;margin-bottom:20px;">
    <div style="font-size:17px;font-weight:bold;margin-bottom:4px;">政府省庁 環境・廃棄物・脱炭素 最新情報</div>
    <div style="font-size:12px;">{report_date} ／ {total} 件</div>
  </div>
  {body_html}
  <p style="font-size:11px;color:#aaa;text-align:center;margin-top:32px;">このメールは自動生成されています。各省庁公式サイトで最新情報をご確認ください。</p>
</body>
</html>"""


def build_text_report(articles: list, report_date: str) -> str:
    lines = [
        f"【政府省庁 環境・廃棄物・脱炭素 最新情報】{report_date}",
        f"総件数: {len(articles)} 件",
        "=" * 60,
    ]
    if not articles:
        lines.append("本日は対象キーワードに合致する情報は見つかりませんでした。")
        return "\n".join(lines)

    ministry_groups: dict = {}
    for a in articles:
        ministry_groups.setdefault(a.ministry, []).append(a)

    for ministry, items in ministry_groups.items():
        lines.append(f"\n■ {ministry}（{len(items)} 件）")
        lines.append("-" * 40)
        for item in items:
            lines.append(f"  [{item.date_str or '日付不明'}] {item.title}")
            lines.append(f"  URL: {item.url}")
            lines.append(f"  キーワード: {', '.join(item.matched_keywords[:5])}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# メール送信
# ---------------------------------------------------------------------------

def send_email(html_body: str, text_body: str, subject: str) -> None:
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    email_from = os.environ.get("EMAIL_FROM", smtp_user)
    email_to = os.environ["EMAIL_TO"]

    recipients = [addr.strip() for addr in email_to.split(",")]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, recipients, msg.as_string())

    logger.info(f"メール送信完了: {', '.join(recipients)}")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now()
    report_date = now.strftime("%Y年%m月%d日 %H:%M")
    subject = f"【省庁ニュース】廃棄物・脱炭素・CO2 {now.strftime('%Y/%m/%d')}"

    logger.info("省庁プレスリリース収集開始")
    articles = collect_all()
    logger.info(f"合計 {len(articles)} 件取得")

    html_body = build_html_report(articles, report_date)
    text_body = build_text_report(articles, report_date)

    report_path = f"report_{now.strftime('%Y%m%d')}.html"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_body)
    logger.info(f"レポート保存: {report_path}")

    required_vars = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
    if all(os.environ.get(v) for v in required_vars):
        send_email(html_body, text_body, subject)
    else:
        logger.warning("メール設定が未設定のため、ターミナル出力のみ")
        print(text_body)


if __name__ == "__main__":
    main()
