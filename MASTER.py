"""
Search Smart - master file: crawler + scraper in ONE file, AWS Lambda ready.

Flow:
  front end --{query, count, ip}--> THIS LAMBDA --{query, count, results}--> ip (front end)
  The same results are also returned in the Lambda's own response.

  1. Google Shopping: asked through SerpApi, which returns product names, prices, sellers
     and links as ready-made data (Google blocks direct requests from scripts).
  2. Other shops (SITES below): crawled directly, politely (robots.txt + delay), and the
     price is read from each product page as it is found.
  3. Everything is de-duplicated, ranked cheapest first, and trimmed to `count`.

Needs ONE extra package: beautifulsoup4 (everything else is the standard library).

Input (JSON body via Function URL / API Gateway, or a direct invoke):
  {"query": "usb c cable", "count": 5, "ip": "203.0.113.20:9000"}

Each result:
  {"name": str, "price": float, "currency": str, "url": str, "store": str}

Lambda settings:
  Runtime   Python 3.12            Handler  search_smart.lambda_handler
  Timeout   60 seconds             Memory   256 MB
  Env vars  SERPAPI_KEY      SerpApi key: turns on Google Shopping (recommended)
            CRAWLBASE_TOKEN  older Google route through Crawlbase (slow, unreliable; leave unset)
            GOOGLE_COUNTRY   country for Google results (default US)
            TIME_LIMIT       seconds to spend searching (default 25)
            REPLY_PATH       path added to the ip when sending results, e.g. /results (default none)
            SEND_RESULTS     0 = only return results, don't POST them to the ip (default 1)
  NOTE: API Gateway cuts requests off at 29 s. Prefer a Lambda Function URL, or invoke the
        Lambda asynchronously, since a Google search through Crawlbase takes several seconds.

Test on your own computer (no AWS):
  pip install beautifulsoup4
  python search_smart.py "usb c cable" --count 5            # add --debug to save Google's page
  (set the token first in PowerShell:  $env:SERPAPI_KEY="your-token")
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse, quote_plus

from bs4 import BeautifulSoup

USER_AGENT = "SearchSmartBot/0.1 (university class project)"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")          # Google Shopping via SerpApi (recommended)
CRAWLBASE_TOKEN = os.environ.get("CRAWLBASE_TOKEN", "")  # older route; unreliable
GOOGLE_COUNTRY = os.environ.get("GOOGLE_COUNTRY", "US")
TIME_LIMIT = float(os.environ.get("TIME_LIMIT", "25"))
REPLY_PATH = os.environ.get("REPLY_PATH", "")
SEND_RESULTS = os.environ.get("SEND_RESULTS", "1") == "1"
MAX_PAGES = 40
MAX_DEPTH = 3
DELAY = 1.0  # seconds between requests to the same shop
DEBUG = False

# ---- EDIT ME: shops to crawl directly. "{q}" becomes the URL-encoded query. ----
# Only use sites whose robots.txt / terms allow automated access.
#   title_must_match: True for sites with no search of their own (keeps only matching products).
SITES = [
    # Practice site built for scraping (no search: same books for any query).
    {"url": "https://books.toscrape.com/catalogue/page-1.html", "title_must_match": False},
    # {"url": "https://some-shop.com/search?q={q}", "title_must_match": False},
]

ERRORS: list = []  # problems seen during one search; returned so failures are visible
STATS: dict = {}   # crawl counters for one search; returned to help with debugging


def note(msg: str) -> None:
    if len(ERRORS) < 6:
        ERRORS.append(msg)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def fetch(url: str, timeout: float = 8):
    """Page text, or None if it can't be fetched or isn't HTML."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.headers.get_content_type() != "text/html":
                return None
            return resp.read(2_000_000).decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        note(f"fetch failed {url}: {e}")
        return None


def fetch_robots(base: str):
    """robots.txt text; "" if the site has none; None if we can't tell."""
    req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read(500_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError:
        return ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        note(f"could not read robots.txt for {base}: {e}")
        return None


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------
_CUR = {"$": "USD", "£": "GBP", "€": "EUR"}
_PRICE = re.compile(r"([£$€])\s?(\d[\d.,]*)|(\d[\d.,]*)\s?([£$€])")


def to_number(s: str):
    """'1,299.99' -> 1299.99, '12,99' -> 12.99, '1,299' -> 1299.0, else None."""
    s = s.strip().rstrip(".,")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = head.replace(",", "") + "." + tail if len(tail) == 2 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def find_price(text: str):
    """First price with a currency symbol in `text` -> (amount, currency) or None."""
    m = _PRICE.search(text or "")
    if not m:
        return None
    amount = to_number(m.group(2) or m.group(3))
    if not amount or amount <= 0:
        return None
    return amount, _CUR[m.group(1) or m.group(4)]


def _to_float(value):
    m = re.search(r"\d[\d.,]*", str(value if value is not None else ""))
    return to_number(m.group()) if m else None


def _product(name, price, currency, url, store):
    name = str(name or "").strip()
    if not name or not price or price <= 0:
        return None
    return {"name": name, "price": round(float(price), 2),
            "currency": str(currency or "USD").upper(), "url": url, "store": store}


# --------------------------------------------------------------------------
# Reading a product page (the "scraper" half)
# --------------------------------------------------------------------------
def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def page_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    return soup.title.string.strip() if soup.title and soup.title.string else ""


def extract_product(soup: BeautifulSoup, url: str):
    """Name + price from one product page, or None if it isn't a product page."""
    store = urlparse(url).netloc.removeprefix("www.")

    # 1) schema.org JSON-LD (most modern shops publish this)
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            types = node.get("@type")
            if "Product" not in (types if isinstance(types, list) else [types]):
                continue
            offers = node.get("offers")
            for offer in (offers if isinstance(offers, list) else [offers or {}]):
                if not isinstance(offer, dict):
                    continue
                price = _to_float(offer.get("price") or offer.get("lowPrice"))
                p = _product(node.get("name"), price, offer.get("priceCurrency", "USD"), url, store)
                if p:
                    return p

    # 2) Open Graph / product meta tags
    meta = (soup.find("meta", property="product:price:amount")
            or soup.find("meta", property="og:price:amount"))
    if meta:
        cur = soup.find("meta", property="product:price:currency")
        p = _product(page_title(soup), _to_float(meta.get("content")),
                     cur.get("content") if cur and cur.get("content") else "USD", url, store)
        if p:
            return p

    # 3) microdata: <... itemprop="price" content="12.99">
    el = soup.find(attrs={"itemprop": "price"})
    if el:
        raw = el.get("content") or el.get_text()
        found = find_price(raw)
        p = _product(page_title(soup), found[0] if found else _to_float(raw),
                     found[1] if found else "USD", url, store)
        if p:
            return p

    # 4) books.toscrape.com (practice site): the product page is <article class="product_page">
    art = soup.select_one("article.product_page")
    el = art.select_one(".price_color") if art else None
    if el:
        found = find_price(el.get_text())
        if found:
            return _product(page_title(soup), found[0], found[1], url, store)
    return None


# --------------------------------------------------------------------------
# Google Shopping, fetched through Crawlbase
# --------------------------------------------------------------------------
_GOOGLE_PRODUCT = re.compile(r"/shopping/product/(\d+)")


def crawlbase_get(url: str, timeout: float):
    api = ("https://api.crawlbase.com/?token=" + quote_plus(CRAWLBASE_TOKEN)
           + "&country=" + quote_plus(GOOGLE_COUNTRY) + "&url=" + quote_plus(url))
    try:
        with urllib.request.urlopen(api, timeout=timeout) as resp:
            return resp.read(3_000_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        note(f"Crawlbase request failed: {e}")
        return None


def parse_google_results(html: str, count: int) -> list:
    """Reads products from a Google Shopping results page.

    Google's CSS class names are random and change often, so this relies on structure instead:
    every link to /shopping/product/<id> is a product; the smallest block around that link
    that shows a price is its card; the card's heading (or the link's label) is the name.
    """
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = _GOOGLE_PRODUCT.search(a["href"])
        if not m or m.group(1) in seen:
            continue
        card, found = a, None
        for _ in range(6):
            card = card.parent
            if card is None:
                break
            found = find_price(card.get_text(" ", strip=True))
            if found:
                break
        if not found:
            continue
        heading = card.find(["h3", "h4"])
        title = ((heading.get_text(" ", strip=True) if heading else "")
                 or (a.get("aria-label") or "").strip()
                 or _PRICE.sub("", a.get_text(" ", strip=True)).strip())
        p = _product(title, found[0], found[1],
                     f"https://www.google.com/shopping/product/{m.group(1)}", "Google Shopping")
        if p:
            seen.add(m.group(1))
            out.append(p)
            if len(out) >= count:
                break
    return out


def serpapi_products(query: str, count: int, timeout: float) -> list:
    """Google Shopping through SerpApi, which returns ready-made product data as JSON."""
    api = ("https://serpapi.com/search.json?engine=google_shopping&gl=us&hl=en"
           "&q=" + quote_plus(query) + "&api_key=" + quote_plus(SERPAPI_KEY))
    try:
        with urllib.request.urlopen(api, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        note(f"SerpApi request failed: {e}")
        return []
    if data.get("error"):
        note(f"SerpApi error: {data['error']}")
    out = []
    for item in data.get("shopping_results") or []:
        found = find_price(str(item.get("price", "")))
        price = item.get("extracted_price")
        if price is None and found:
            price = found[0]
        p = _product(item.get("title"), price, found[1] if found else "USD",
                     item.get("product_link") or item.get("link") or "",
                     item.get("source") or "Google Shopping")
        if p and p["url"]:
            out.append(p)
            if len(out) >= count:
                break
    if not out:
        note("SerpApi returned no shopping results")
    return out


def google_products(query: str, count: int, timeout: float = 20) -> list:
    if SERPAPI_KEY:
        return serpapi_products(query, count, timeout)
    if not CRAWLBASE_TOKEN:
        note("Neither SERPAPI_KEY nor CRAWLBASE_TOKEN is set, so Google Shopping was skipped")
        return []
    html = crawlbase_get("https://www.google.com/search?tbm=shop&q=" + quote_plus(query), timeout)
    if not html:
        return []
    if DEBUG:
        with open("google_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
    products = parse_google_results(html, count)
    if not products:
        note("Google page was fetched but no products could be read from it")
    return products


# --------------------------------------------------------------------------
# Crawling the other shops (the "crawler" half)
# --------------------------------------------------------------------------
def extract_links(soup: BeautifulSoup, base_url: str):
    """Yields (link, hot). A link is "hot" when the block around it shows a price - on a
    listing page that is almost always a product card, so the crawler visits those first
    instead of wandering through menus and category pages."""
    for a in soup.find_all("a", href=True):
        link, _ = urldefrag(urljoin(base_url, a["href"]))
        if urlparse(link).scheme not in ("http", "https"):
            continue
        hot, node = False, a
        for _ in range(3):
            node = node.parent
            if node is None:
                break
            if find_price(node.get_text(" ", strip=True)):
                hot = True
                break
        yield link, hot


def crawl_shops(query: str, count: int, time_limit: float) -> list:
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    deadline = time.monotonic() + time_limit
    frontier, hot, seen, hosts = deque(), deque(), set(), {}
    for site in SITES:
        url = site["url"].replace("{q}", quote_plus(query))
        frontier.append((url, 0))
        seen.add(url)
        hosts[urlparse(url).netloc] = site

    robots, last_hit, found, found_urls, pages, skipped = {}, {}, [], set(), 0, 0

    def allowed(url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in robots:
            rp = robotparser.RobotFileParser()
            text = fetch_robots(base)
            rp.parse(text.splitlines() if text is not None else ["User-agent: *", "Disallow: /"])
            robots[base] = rp
        return robots[base].can_fetch(USER_AGENT, url)

    while (hot or frontier) and len(found) < count and pages < MAX_PAGES \
            and time.monotonic() < deadline:
        url, depth = (hot or frontier).popleft()  # likely product links first
        if not allowed(url):
            skipped += 1
            continue
        host = urlparse(url).netloc
        time.sleep(max(0, last_hit.get(host, 0) + DELAY - time.monotonic()))
        last_hit[host] = time.monotonic()
        pages += 1

        html = fetch(url)
        if html is None:
            continue
        soup = BeautifulSoup(html, "html.parser")

        product = extract_product(soup, url)
        if product and url not in found_urls:
            text = f"{product['name']} {url}".lower()
            if not hosts.get(host, {}).get("title_must_match") or any(w in text for w in words):
                found.append(product)
                found_urls.add(url)

        if depth < MAX_DEPTH:
            for link, is_hot in extract_links(soup, url):
                if urlparse(link).netloc in hosts and link not in seen:  # stay on the shops' domains
                    seen.add(link)
                    (hot if is_hot else frontier).append((link, depth + 1))
    STATS.update(pages_fetched=pages, skipped_by_robots=skipped, products_found=len(found),
                 pages_still_queued=len(frontier) + len(hot), seconds_left=round(deadline - time.monotonic(), 1))
    return found


# --------------------------------------------------------------------------
# One search, end to end
# --------------------------------------------------------------------------
def clean(products: list, count: int) -> list:
    seen, out = set(), []
    for p in products:
        if p and p["url"] not in seen and p["price"] > 0:
            seen.add(p["url"])
            out.append(p)
    # cheapest first, kept separate by currency so USD is never ranked against EUR
    out.sort(key=lambda p: (p["currency"], p["price"]))
    return out[:count]


def run_search(query: str, count: int, time_limit: float) -> dict:
    del ERRORS[:]
    STATS.clear()
    start = time.monotonic()
    google = google_products(query, count, timeout=min(20, max(5, time_limit)))
    left = time_limit - (time.monotonic() - start)
    shops = crawl_shops(query, count - len(google), left) if len(google) < count and left >= 2 else []
    results = clean(google + shops, count)
    return {"query": query, "count": len(results), "results": results,
            "sources": {"google_shopping": len(google), "crawled_shops": len(shops)},
            "crawl": dict(STATS), "errors": list(ERRORS)}


# --------------------------------------------------------------------------
# Lambda entry point
# --------------------------------------------------------------------------
def normalize_ip(ip: str) -> str:
    """'203.0.113.20:9000' -> 'http://203.0.113.20:9000'. Rejects anything odd."""
    ip = str(ip).strip()
    if "://" not in ip:
        ip = "http://" + ip
    p = urlparse(ip)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("ip must look like 203.0.113.20:9000 or http://host:port")
    return ip.rstrip("/")


def parse_event(event: dict):
    body = event.get("body", event) if isinstance(event, dict) else {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            raise ValueError("body must be JSON")
    query = " ".join(str(body.get("query", "")).split())
    if not 1 <= len(query) <= 100:
        raise ValueError("query must be 1-100 characters")
    try:
        count = int(body.get("count"))
    except (TypeError, ValueError):
        raise ValueError("count must be a number")
    if not 1 <= count <= 50:
        raise ValueError("count must be between 1 and 50")
    if not body.get("ip"):
        raise ValueError("ip is required")
    return query, count, normalize_ip(body["ip"])


def respond(status: int, body: dict) -> dict:
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json",
                        "Access-Control-Allow-Origin": "*"},
            "body": json.dumps(body)}


def lambda_handler(event, context):
    try:
        query, count, reply_to = parse_event(event)
    except ValueError as e:
        return respond(400, {"error": str(e)})

    # keep ~8 s of the Lambda timeout for sending the results on
    remaining = context.get_remaining_time_in_millis() / 1000 if context else 60
    time_limit = max(3.0, min(TIME_LIMIT, remaining - 8))

    out = run_search(query, count, time_limit)
    out["sent_to_ip"] = False

    if out["results"] and SEND_RESULTS:
        payload = json.dumps({"query": query, "count": out["count"],
                              "results": out["results"]}).encode()
        req = urllib.request.Request(reply_to + REPLY_PATH, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=6):
                pass
            out["sent_to_ip"] = True
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            out["errors"].append(f"could not send results to {reply_to + REPLY_PATH}: {e}")
    return respond(200, out)


# Local test, no AWS needed:  python search_smart.py "usb c cable" --count 5 [--debug]
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Run one search and print the results.")
    ap.add_argument("query", nargs="?", default="book")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--debug", action="store_true", help="save Google's page to google_debug.html")
    args = ap.parse_args()
    DEBUG = args.debug
    print(json.dumps(run_search(args.query, args.count, TIME_LIMIT), indent=2))