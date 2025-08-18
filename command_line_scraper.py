# command_line_scraper.py
# A command-line version of the Firecrawl-style scraper with SERP analysis.
# This script is designed to be executed directly from the terminal or via n8n's "Execute Command" node.
# It scrapes or crawls a URL and prints the result as a JSON object to standard output.

import os
import re
import time
import logging
import random
import json
import argparse
import asyncio
import base64
from collections import deque
from urllib.parse import urlparse, urljoin, urldefrag
from typing import List, Optional, Dict, Set, Any

# Third-party imports
from pydantic import BaseModel, Field
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import html2text
from openai import AsyncOpenAI

# --- Configuration ---
# Environment variables are loaded by the n8n environment on Render.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATA_FOR_SEO_LOGIN = os.getenv("DATA_FOR_SEO_LOGIN")
DATA_FOR_SEO_PASSWORD = os.getenv("DATA_FOR_SEO_PASSWORD")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Pydantic Models for Data Structure ---
class ScrapePageOptions(BaseModel):
    ai_analysis: bool = Field(False)
    ai_prompt: Optional[str] = Field("Summarize this content in 3 bullet points.")
    client_summary: Optional[List[str]] = None

class CrawlerOptions(BaseModel):
    max_pages: int = Field(20)
    max_depth: int = Field(3)
    delay_seconds: float = Field(1.0)
    same_domain_only: bool = Field(True)
    respect_robots: bool = Field(True)
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = Field(default_factory=list)

class ScrapeResult(BaseModel):
    url: str
    status: str
    markdown: Optional[str] = None
    metadata: Dict[str, Any] = {}
    ai_analysis: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class CrawlResponse(BaseModel):
    status: str
    start_url: str
    total_pages_crawled: int
    results: List[ScrapeResult]

class SerpResult(BaseModel):
    keyword: str
    url: str
    status: str
    markdown: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    ai_analysis: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class SerpResponse(BaseModel):
    status: str
    keywords_processed: List[str]
    results: List[SerpResult]

# --- Helper Functions ---
def make_resilient_request(url: str, timeout: int = 15) -> requests.Response:
    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    session.headers.update(headers)
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response

def html_to_markdown(html_content: str) -> str:
    if not html_content: return ""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.body_width = 0
    return h.handle(html_content)

def extract_metadata(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    metadata = {}
    title_tag = soup.find('title')
    metadata['title'] = title_tag.get_text().strip() if title_tag else ""
    desc_tag = soup.find('meta', attrs={'name': 'description'})
    metadata['description'] = desc_tag.get('content', '').strip() if desc_tag else ""
    h1_tags = soup.find_all('h1')
    metadata['h1_tags'] = [h1.get_text().strip() for h1 in h1_tags]
    h2_tags = soup.find_all('h2')
    metadata['h2_tags'] = [h2.get_text().strip() for h2 in h2_tags[:10]]
    visible_text = soup.get_text()
    metadata['word_count'] = len(visible_text.split())
    return metadata

def extract_links(soup: BeautifulSoup, base_url: str) -> Set[str]:
    links = set()
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        if href:
            absolute_url = urljoin(base_url, href)
            absolute_url = urldefrag(absolute_url)[0]
            links.add(absolute_url)
    return links

def is_valid_url(url: str, options: CrawlerOptions, base_domain: str) -> bool:
    try:
        parsed_url = urlparse(url)
        if parsed_url.scheme not in ['http', 'https']: return False
        if options.same_domain_only and parsed_url.netloc != base_domain: return False
        if options.exclude_patterns and any(re.search(p, url, re.I) for p in options.exclude_patterns): return False
        if options.include_patterns and not any(re.search(p, url, re.I) for p in options.include_patterns): return False
        return True
    except Exception:
        return False

def get_dataforseo_serp(keyword: str, location_code: int, num_results: int = 5) -> List[str]:
    if not DATA_FOR_SEO_LOGIN or not DATA_FOR_SEO_PASSWORD:
        logging.error("❌ DataForSEO credentials not set in environment variables.")
        return []
    
    auth = base64.b64encode(f"{DATA_FOR_SEO_LOGIN}:{DATA_FOR_SEO_PASSWORD}".encode()).decode("utf-8")
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    payload = [{"keyword": keyword, "location_code": location_code, "language_name": "English", "depth": num_results}]
    
    try:
        response = requests.post("https://api.dataforseo.com/v3/serp/google/organic/live/regular", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        items = result.get("tasks", [{}])[0].get("result", [{}])[0].get("items", [])
        urls = [item.get("url") for item in items if item.get("url")]
        logging.info(f"✅ Found {len(urls)} URLs from DataForSEO for keyword '{keyword}'")
        return urls
    except Exception as e:
        logging.error(f"❌ DataForSEO API call failed: {e}")
        return []

async def perform_ai_analysis(page_content: str, prompt: str) -> (Optional[str], Optional[str]):
    if not OPENAI_API_KEY:
        logging.warning("⚠️ OpenAI API key not found. Skipping AI summary.")
        return None, "N/A"
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert SEO and content analyst."},
                {"role": "user", "content": f"{prompt}\n\n---\n\nCONTENT:\n{page_content[:8000]}"}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content), "gpt-4o"
    except Exception as e:
        logging.error(f"❌ OpenAI call failed: {e}")
        return {"error": f"AI analysis failed: {e}"}, "N/A"

# --- Core Logic ---
async def scrape_url(url: str, options: ScrapePageOptions) -> ScrapeResult:
    try:
        response = make_resilient_request(url)
        html_content = response.text
        markdown_content = html_to_markdown(html_content)
        metadata = extract_metadata(html_content)
        
        ai_analysis = None
        model_used = "N/A"
        if options.ai_analysis:
            logging.info(f"🤖 Performing AI analysis for {url}...")
            prompt = options.ai_prompt.format(
                client_summary=", ".join(options.client_summary or ["Not provided"]),
                page_content=markdown_content[:4000]
            )
            ai_analysis, model_used = await perform_ai_analysis(markdown_content, prompt)
            
        return ScrapeResult(url=url, status="success", markdown=markdown_content, metadata=metadata, ai_analysis={"summary": ai_analysis, "model_used": model_used})
    except Exception as e:
        return ScrapeResult(url=url, status="error", error=str(e))

async def crawl_website(start_url: str, crawl_options: CrawlerOptions, page_options: ScrapePageOptions) -> CrawlResponse:
    base_domain = urlparse(start_url).netloc
    queue = deque([(start_url, 0)])
    visited_urls = {start_url}
    scraped_results = []

    while queue and len(scraped_results) < crawl_options.max_pages:
        current_url, current_depth = queue.popleft()
        if current_depth > crawl_options.max_depth: continue

        logging.info(f"Crawling [{len(scraped_results) + 1}/{crawl_options.max_pages}] URL: {current_url} (Depth: {current_depth})")
        scrape_result = await scrape_url(current_url, page_options)
        scraped_results.append(scrape_result)

        if scrape_result.status == "success" and current_depth < crawl_options.max_depth:
            try:
                response = make_resilient_request(current_url)
                soup = BeautifulSoup(response.text, 'html.parser')
                new_links = extract_links(soup, current_url)
                for link in new_links:
                    if link not in visited_urls and is_valid_url(link, crawl_options, base_domain):
                        visited_urls.add(link)
                        queue.append((link, current_depth + 1))
            except Exception as e:
                logging.warning(f"⚠️ Could not extract links from {current_url}: {e}")

        time.sleep(crawl_options.delay_seconds)

    return CrawlResponse(status="completed", start_url=start_url, total_pages_crawled=len(scraped_results), results=scraped_results)

async def serp_scrape(keywords: List[str], location_code: int, num_results: int) -> SerpResponse:
    all_results = []
    for keyword in keywords:
        urls = get_dataforseo_serp(keyword, location_code, num_results)
        for url in urls:
            logging.info(f"Scraping SERP result for '{keyword}': {url}")
            try:
                response = make_resilient_request(url)
                html_content = response.text
                markdown_content = html_to_markdown(html_content)
                metadata = extract_metadata(html_content)
                
                prompt = f"Analyze this competitor page that ranks for the keyword '{keyword}'. Summarize their content strategy, main topics, and page structure in a JSON object with keys 'page_topic', 'relevant_keywords', and 'strategy_summary'."
                ai_analysis, model_used = await perform_ai_analysis(markdown_content, prompt)

                all_results.append(SerpResult(
                    keyword=keyword, url=url, status="success", markdown=markdown_content,
                    metadata=metadata, ai_analysis={"summary": ai_analysis, "model_used": model_used}
                ))
            except Exception as e:
                all_results.append(SerpResult(keyword=keyword, url=url, status="error", error=str(e)))
            time.sleep(1.0) # Delay between scraping SERP results
            
    return SerpResponse(status="completed", keywords_processed=keywords, results=all_results)

# --- Command-Line Interface (CLI) ---
async def main():
    parser = argparse.ArgumentParser(description="A Firecrawl-style web scraper and crawler.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- Crawl Command ---
    p_crawl = subparsers.add_parser("crawl", help="Crawl a website.")
    p_crawl.add_argument("--url", required=True)
    p_crawl.add_argument("--max-pages", type=int, default=20)
    p_crawl.add_argument("--max-depth", type=int, default=3)
    p_crawl.add_argument("--delay-seconds", type=float, default=1.0)
    p_crawl.add_argument("--ai-analysis", action="store_true")
    p_crawl.add_argument("--client-summary", type=str, default="")
    p_crawl.add_argument("--exclude-patterns", nargs='*', default=[])

    # --- SERP Command ---
    p_serp = subparsers.add_parser("serp", help="Scrape SERP results for keywords.")
    p_serp.add_argument("--keywords", nargs='+', required=True)
    p_serp.add_argument("--location-code", type=int, default=2840)
    p_serp.add_argument("--num-results", type=int, default=5)

    args = parser.parse_args()

    if args.command == "crawl":
        page_options = ScrapePageOptions(
            ai_analysis=args.ai_analysis,
            client_summary=args.client_summary.split(';') if args.client_summary else []
        )
        crawl_options = CrawlerOptions(
            max_pages=args.max_pages, max_depth=args.max_depth,
            delay_seconds=args.delay_seconds, exclude_patterns=args.exclude_patterns
        )
        result = await crawl_website(args.url, crawl_options, page_options)
        print(result.model_dump_json(indent=2))

    elif args.command == "serp":
        result = await serp_scrape(args.keywords, args.location_code, args.num_results)
        print(result.model_dump_json(indent=2))

if __name__ == "__main__":
    asyncio.run(main())
