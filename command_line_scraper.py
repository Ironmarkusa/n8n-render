# command_line_scraper.py
# A command-line version of the Firecrawl-style scraper.
# This script is designed to be executed directly from the terminal or via n8n's "Execute Command" node.
# It scrapes or crawls a URL and prints the result as a JSON object to standard output.
#
# To Run This Script:
# 1. Install necessary libraries:
#    pip install python-dotenv requests beautifulsoup4 html2text openai
#
# 2. Create a .env file in the same directory with your OpenAI API key:
#    OPENAI_API_KEY="your_openai_api_key_here"
#
# 3. Run from your terminal:
#    - To scrape: python command_line_scraper.py scrape --url "https://example.com"
#    - To crawl:  python command_line_scraper.py crawl --url "https://example.com" --max-pages 10 --exclude-patterns "/login" "/admin"

import os
import re
import time
import logging
import random
import json
import argparse
import asyncio
from collections import deque
from urllib.parse import urlparse, urljoin, urldefrag
from typing import List, Optional, Dict, Set

# Third-party imports
from pydantic import BaseModel, HttpUrl, Field
from dotenv import load_dotenv
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import html2text
from openai import AsyncOpenAI

# --- Configuration ---
load_dotenv()

# Load environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Pydantic Models for Data Structure ---
# These models help structure the data internally and for the final JSON output.

class ScrapePageOptions(BaseModel):
    """Options for how a page is scraped."""
    ai_analysis: bool = Field(False, description="If true, perform AI analysis to summarize the content.")
    ai_prompt: Optional[str] = Field(
        "Summarize the following content in 3 concise bullet points, capturing the main topic and key takeaways.",
        description="The prompt to use for the AI analysis."
    )

class CrawlerOptions(BaseModel):
    """Options for the crawler's behavior."""
    max_pages: int = Field(20, description="Maximum number of pages to crawl.")
    max_depth: int = Field(3, description="Maximum depth to crawl from the start URL.")
    delay_seconds: float = Field(1.0, description="Delay between requests to be respectful to the server.")
    same_domain_only: bool = Field(True, description="Only crawl links on the same domain as the start URL.")
    respect_robots: bool = Field(True, description="Respect the site's robots.txt file (currently a placeholder).")
    include_patterns: Optional[List[str]] = Field(None, description="List of regex patterns to include URLs.")
    exclude_patterns: Optional[List[str]] = Field(
        [r'/login', r'/admin', r'/cart', r'#'],
        description="List of regex patterns to exclude URLs."
    )

class ScrapeResult(BaseModel):
    """Response model for a single scraped page."""
    url: str
    status: str
    markdown: Optional[str] = None
    metadata: Dict[str, Optional[str]] = {}
    ai_summary: Optional[str] = None
    model_used: Optional[str] = None
    error: Optional[str] = None

class CrawlResponse(BaseModel):
    """Response model for a crawl operation."""
    status: str
    start_url: str
    total_pages_crawled: int
    results: List[ScrapeResult]

# --- Helper Functions (Identical to the FastAPI version) ---

def make_resilient_request(url: str, timeout: int = 15) -> requests.Response:
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
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

def extract_metadata(soup: BeautifulSoup) -> Dict[str, Optional[str]]:
    title = soup.title.string.strip() if soup.title else None
    description = soup.find('meta', attrs={'name': 'description'})
    return {"title": title, "description": description['content'].strip() if description else None}

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

async def get_ai_summary(content: str, prompt: str, api_key: str) -> (Optional[str], Optional[str]):
    if not api_key:
        logging.warning("⚠️ OpenAI API key not found. Skipping AI summary.")
        return None, None
    client = AsyncOpenAI(api_key=api_key)
    system_prompt = "You are an expert content analyst. Your goal is to provide a clear and concise summary based on the user's request."
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{prompt}\n\n---\n\nCONTENT:\n{content[:8000]}"}
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip(), "gpt-4o"
    except Exception as e:
        logging.error(f"❌ OpenAI call failed: {e}")
        return f"AI analysis failed: {e}", "N/A"

# --- Core Scraping and Crawling Logic ---

async def scrape_url(url: str, options: ScrapePageOptions) -> ScrapeResult:
    try:
        response = make_resilient_request(url)
        html_content = response.text
        soup = BeautifulSoup(html_content, 'html.parser')
        markdown_content = html_to_markdown(html_content)
        metadata = extract_metadata(soup)
        ai_summary, model_used = (await get_ai_summary(markdown_content, options.ai_prompt, OPENAI_API_KEY)) if options.ai_analysis else (None, None)
        return ScrapeResult(url=url, status="success", markdown=markdown_content, metadata=metadata, ai_summary=ai_summary, model_used=model_used)
    except requests.exceptions.RequestException as e:
        return ScrapeResult(url=url, status="error", error=f"HTTP request failed: {e}")
    except Exception as e:
        return ScrapeResult(url=url, status="error", error=f"An unexpected error occurred: {e}")

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
                # We need to re-fetch the content to parse links, as scrape_url only returns markdown
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

# --- Command-Line Interface (CLI) ---

async def main():
    """
    Main function to parse command-line arguments and trigger the appropriate action.
    """
    parser = argparse.ArgumentParser(description="A Firecrawl-style web scraper and crawler.")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # --- Scrape Command ---
    parser_scrape = subparsers.add_parser("scrape", help="Scrape a single URL.")
    parser_scrape.add_argument("--url", required=True, help="The URL to scrape.")
    parser_scrape.add_argument("--ai-analysis", action="store_true", help="Enable AI summary of the page content.")

    # --- Crawl Command ---
    parser_crawl = subparsers.add_parser("crawl", help="Crawl a website from a starting URL.")
    parser_crawl.add_argument("--url", required=True, help="The starting URL for the crawl.")
    parser_crawl.add_argument("--max-pages", type=int, default=20, help="Maximum number of pages to crawl.")
    parser_crawl.add_argument("--max-depth", type=int, default=3, help="Maximum crawl depth.")
    parser_crawl.add_argument("--delay-seconds", type=float, default=1.0, help="Delay between requests.")
    parser_crawl.add_argument("--ai-analysis", action="store_true", help="Enable AI summary for each crawled page.")
    parser_crawl.add_argument("--same-domain-only", dest='same_domain_only', action='store_true', help="Crawl only the start domain (default).")
    parser_crawl.add_argument("--no-same-domain-only", dest='same_domain_only', action='store_false', help="Allow crawling other domains.")
    parser_crawl.add_argument("--respect-robots", dest='respect_robots', action='store_true', help="Respect robots.txt (default).")
    parser_crawl.add_argument("--no-respect-robots", dest='respect_robots', action='store_false', help="Ignore robots.txt.")
    parser_crawl.add_argument("--include-patterns", nargs='*', default=None, help="List of patterns to include.")
    parser_crawl.add_argument("--exclude-patterns", nargs='*', default=['/login', '/admin', '/cart', '#'], help="List of patterns to exclude.")
    parser_crawl.set_defaults(same_domain_only=True, respect_robots=True)
    
    args = parser.parse_args()

    if args.command == "scrape":
        page_options = ScrapePageOptions(ai_analysis=args.ai_analysis)
        result = await scrape_url(args.url, page_options)
        print(result.model_dump_json(indent=2))

    elif args.command == "crawl":
        page_options = ScrapePageOptions(ai_analysis=args.ai_analysis)
        crawl_options = CrawlerOptions(
            max_pages=args.max_pages, 
            max_depth=args.max_depth,
            delay_seconds=args.delay_seconds,
            same_domain_only=args.same_domain_only,
            respect_robots=args.respect_robots,
            include_patterns=args.include_patterns,
            exclude_patterns=args.exclude_patterns
        )
        result = await crawl_website(args.url, crawl_options, page_options)
        print(result.model_dump_json(indent=2))

if __name__ == "__main__":
    # This allows the async main function to be run from the command line.
    asyncio.run(main())
