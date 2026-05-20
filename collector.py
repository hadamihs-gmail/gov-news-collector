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
from supabase import create_client, Client

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
        "link_path_prefix": "/press/",
    },
    {
        "name": "経済産業省",
        "url": "https://www.meti.go.jp/press/",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.meti.go.jp",
        "link_path_prefix": "/press/",
    },
    {
        "name": "国土交通省",
        "url": "https://www.mlit.go.jp/report/press/index.html",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.mlit.go.jp",
        "link_path_prefix": "/report/press/",
    },
    {
        "name": "農林水産省",
        "url": "https://www.maff.go.jp/j/press/",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.maff.go.jp",
        "link_path_prefix": "/j/press/",
    },
    {
        "name": "内閣府",
        "url": "https://www.cao.go.jp/press/index.html",
        "encoding": "utf-8",
        "item_selector": "li",
        "link_base": "https://www.cao.go.jp",
        "link_path_prefix": "/press/",
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
    seen_count: int = 1


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
            path = href.replace(source["link_base"], "")
        elif href.startswith("/"):
            full_url = source["link_base"] + href
            path = href
        else:
            full_url = source["link_base"] + "/" + href
            path = "/" + href

        prefix = source.get("link_path_prefix")
        if prefix and not path.startswith(prefix):
            continue

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

def _article_cards(items: list, show_count: bool = False) -> str:
    html = ""
    ministry_groups: dict = {}
    for a in items:
        ministry_groups.setdefault(a.ministry, []).append(a)
    for ministry, group in ministry_groups.items():
        html += f'<h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:4px;font-size:16px;margin:24px 0 12px;">{ministry}（{len(group)} 件）</h2>'
        for item in group:
            kw_badges = " ".join(
                f'<span style="display:inline-block;background:#d5e8d4;color:#274e13;border-radius:3px;'
                f'padding:2px 7px;font-size:11px;margin:2px 2px 2px 0;">{kw}</span>'
                for kw in item.matched_keywords[:5]
            )
            count_badge = (
                f'<span style="display:inline-block;background:#f0e0d6;color:#7e3c00;border-radius:3px;'
                f'padding:2px 7px;font-size:11px;margin-bottom:4px;">過去{item.seen_count}回掲載</span> '
                if show_count else ""
            )
            html += f"""
<div style="background:#f9f9f9;border:1px solid #e0e0e0;border-radius:6px;padding:12px 14px;margin-bottom:10px;">
  <div style="font-size:11px;color:#888;margin-bottom:4px;">{item.date_str or "—"}</div>
  <div style="margin-bottom:6px;">{count_badge}<a href="{item.url}" style="color:#1a5276;font-size:14px;line-height:1.5;">{item.title}</a></div>
  <div>{kw_badges}</div>
</div>"""
    return html


def build_html_report(articles: list, report_date: str, new_articles: list = None, duplicate_articles: list = None) -> str:
    use_db = new_articles is not None

    if use_db:
        new_html = _article_cards(new_articles) if new_articles else '<p style="color:#888;">本日の新着情報はありませんでした。</p>'
        dup_html = _article_cards(duplicate_articles, show_count=True) if duplicate_articles else '<p style="color:#888;">重複なし</p>'
        body_html = f"""
<h1 style="font-size:15px;color:#1a5276;margin:0 0 12px;">🆕 新着情報（{len(new_articles)} 件）</h1>
{new_html}
<h1 style="font-size:15px;color:#856404;margin:24px 0 12px;border-top:1px solid #ddd;padding-top:20px;">🔁 既出情報（{len(duplicate_articles)} 件）</h1>
{dup_html}"""
        total = len(new_articles) + len(duplicate_articles)
    else:
        body_html = _article_cards(articles) if articles else '<p style="color:#888;">本日は対象キーワードに合致する情報は見つかりませんでした。</p>'
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
# Supabase連携
# ---------------------------------------------------------------------------

def get_supabase_client() -> Optional[Client]:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def sync_articles_with_db(articles: list, client: Client) -> tuple[list, list]:
    """新規記事と重複記事に分類してDBを更新する。新規・重複のリストを返す。"""
    new_articles = []
    duplicate_articles = []
    today = datetime.now().strftime("%Y-%m-%d")

    for article in articles:
        resp = client.table("articles").select("id,seen_count").eq("url", article.url).execute()
        if resp.data:
            row = resp.data[0]
            new_count = row["seen_count"] + 1
            client.table("articles").update({
                "last_seen_at": today,
                "seen_count": new_count,
                "title": article.title,
                "matched_keywords": article.matched_keywords,
            }).eq("id", row["id"]).execute()
            article.seen_count = new_count
            duplicate_articles.append(article)
        else:
            client.table("articles").insert({
                "url": article.url,
                "title": article.title,
                "ministry": article.ministry,
                "date_str": article.date_str,
                "matched_keywords": article.matched_keywords,
                "first_seen_at": today,
                "last_seen_at": today,
                "seen_count": 1,
            }).execute()
            article.seen_count = 1
            new_articles.append(article)

    return new_articles, duplicate_articles


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

    db = get_supabase_client()
    if db:
        logger.info("Supabase連携: 新規・重複を分類中")
        new_articles, duplicate_articles = sync_articles_with_db(articles, db)
        logger.info(f"新規: {len(new_articles)} 件 / 重複: {len(duplicate_articles)} 件")
        html_body = build_html_report(articles, report_date, new_articles, duplicate_articles)
        text_body = build_text_report(new_articles, report_date)
    else:
        logger.warning("Supabase未設定: 全件をレポート")
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
