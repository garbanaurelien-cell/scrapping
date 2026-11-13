"""Simulation locale ou extraction HTTP avec --url."""
import argparse
from html.parser import HTMLParser
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, asdict
from itertools import cycle
import json
import math
import os
import random
import re
import time
from typing import Protocol


@dataclass(frozen=True, repr=False)
class ProxyConfig:
    server: str
    username: str = ''
    password: str = ''

    @classmethod
    def from_environment(cls):
        server = os.environ.get('SCRAPE_PROXY_SERVER', '').strip()
        if not server:
            return None
        parsed = urllib.parse.urlsplit(server)
        if (parsed.scheme != 'http' or not parsed.hostname or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('SCRAPE_PROXY_SERVER doit etre http://hote:port, sans identifiants')
        username = os.environ.get('SCRAPE_PROXY_USERNAME', '')
        password = os.environ.get('SCRAPE_PROXY_PASSWORD', '')
        if password and not username:
            raise ValueError('SCRAPE_PROXY_USERNAME requis avec un mot de passe')
        return cls(server.rstrip('/'), username, password)

    def browser_options(self):
        options = {'server': self.server}
        if self.username:
            options.update(username=self.username, password=self.password)
        return options


def make_opener(proxy=None):
    if not proxy:
        return urllib.request.build_opener()
    address = proxy.server
    if proxy.username:
        parsed = urllib.parse.urlsplit(address)
        credentials = urllib.parse.quote(proxy.username, safe='') + ':' + urllib.parse.quote(proxy.password, safe='')
        address = urllib.parse.urlunsplit((parsed.scheme, credentials + '@' + parsed.netloc, '', '', ''))
    return urllib.request.build_opener(urllib.request.ProxyHandler({'http': address, 'https': address}))


def sample_pause(bounds):
    """Mostly shorter pauses, occasionally longer; always within user bounds."""
    low, high = bounds
    if low == high:
        return low
    split = low + (high - low) * 0.55
    if random.random() < 0.8:
        return random.uniform(low, split)
    return random.uniform(split, high)


@dataclass(frozen=True)
class BrowserProfile:
    name: str
    user_agent: str
    language: str
    platform: str
    viewport: tuple[int, int]
    timezone: str

    def headers(self):
        return {'User-Agent': self.user_agent, 'Accept': 'text/html',
                'Accept-Language': self.language}

@dataclass(frozen=True)
class ProxyExit:
    name: str
    country: str
    address: str

@dataclass(frozen=True)
class Session:
    id: int
    browser: BrowserProfile
    proxy: ProxyExit

class SessionPool:
    def __init__(self, profiles, proxies):
        if not profiles or not proxies:
            raise ValueError('Profiles and proxies must be nonempty')
        self.profiles = cycle(profiles)
        self.proxies = proxies
        self.routes = {}
        self.counter = 0

    def new_session(self, country):
        country = country.upper()
        if country not in self.routes:
            eligible = [p for p in self.proxies if p.country == country]
            if not eligible:
                raise ValueError(f'No exit configured for {country}')
            self.routes[country] = cycle(eligible)
        self.counter += 1
        return Session(self.counter, next(self.profiles), next(self.routes[country]))

@dataclass(frozen=True)
class RequestPlan:
    resource: str
    session: Session | None
    headers: dict[str, str]

class Transport(Protocol):
    def execute(self, plan: RequestPlan) -> dict: ...

class SimulatedTransport:
    def __init__(self, allowed_countries):
        self.allowed_countries = set(allowed_countries)

    def execute(self, plan):
        allowed = plan.session.proxy.country in self.allowed_countries
        return {'simulated': True, 'network_used': False,
                'status': 200 if allowed else 403,
                'decision': 'allowed' if allowed else 'geo_policy_denied',
                'plan': asdict(plan)}

class HttpTransport:
    """One ordinary HTTP GET; demo proxy/profile settings are not used."""
    def __init__(self, timeout=30, proxy=None):
        self.timeout = timeout
        self.opener = make_opener(proxy)

    def execute(self, plan):
        parsed = urllib.parse.urlsplit(plan.resource)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Expected an HTTP(S) URL')
        request = urllib.request.Request(plan.resource, headers={
            'User-Agent': 'ScrapeLearningDemo/1.0', 'Accept': 'text/html'})
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError('Response exceeds 5 MB limit')
            if response.headers.get_content_type() not in ('text/html', 'application/xhtml+xml'):
                raise ValueError('Expected an HTML response')
            return {'status': response.status, 'url': response.url,
                    'html': raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')}


class BrowserTransport:
    """Render JavaScript and expose ordinary DOM image sources."""
    def __init__(self, timeout=30, wait_ms=2000, scroll_steps=0, delay=(3.0, 22.0), diagnostics=None,
                 reject_cookies=False, full_page=False, proxy=None):
        self.timeout = timeout
        self.wait_ms = wait_ms
        self.scroll_steps = scroll_steps
        self.delay = delay
        self.diagnostics = Path(diagnostics) if diagnostics else None
        self.reject_cookies = reject_cookies
        self.full_page = full_page
        self.proxy = proxy

    def execute(self, plan):
        parsed = urllib.parse.urlsplit(plan.resource)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Expected an HTTP(S) URL')
        try:
            from playwright.sync_api import sync_playwright, Error
        except ImportError as error:
            raise ValueError('Installer : python -m pip install playwright ; python -m playwright install chromium') from error
        try:
            with sync_playwright() as playwright:
                options = {'proxy': self.proxy.browser_options()} if self.proxy else {}
                browser = playwright.chromium.launch(**options)
                try:
                    # Isolated, nonpersistent context: no imported cookies/history.
                    context = browser.new_context(accept_downloads=False)
                    page = context.new_page()
                    response = page.goto(plan.resource, timeout=self.timeout * 1000,
                                         wait_until='domcontentloaded')
                    if response is None or response.status >= 400:
                        raise ValueError(f'Navigation refusee : {response.status if response else "aucune reponse"}')
                    page.wait_for_timeout(self.wait_ms)
                    consent_action = 'not_requested'
                    if self.reject_cookies:
                        reject = page.get_by_role('button', name=re.compile(
                            r'^\s*(Reject All|Tout refuser|Refuser tout)\s*$', re.I))
                        if reject.count() == 1 and reject.is_visible():
                            reject.click(timeout=self.timeout * 1000)
                            page.wait_for_timeout(500)
                            consent_action = 'reject_clicked'
                        else:
                            consent_action = 'reject_button_not_found_or_ambiguous'
                        print(f'Cookies : {consent_action}')
                    # Bounded scrolling helps ordinary lazy-loaded images appear.
                    for _ in range(self.scroll_steps):
                        page.mouse.wheel(0, random.randint(300, 650))
                        page.wait_for_timeout(sample_pause(self.delay) * 1000)
                    if self.diagnostics:
                        self.diagnostics.mkdir(parents=True, exist_ok=True)
                        elements = page.eval_on_selector_all('img, canvas', '''elements => elements.map((el, index) => {
                            const r = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return {index, tag: el.tagName.toLowerCase(), id: el.id,
                                class: el.getAttribute('class'), alt: el.getAttribute('alt'),
                                source: el.currentSrc || el.getAttribute('src'),
                                width: r.width, height: r.height, x: r.x, y: r.y,
                                naturalWidth: el.naturalWidth ?? null,
                                naturalHeight: el.naturalHeight ?? null,
                                visible: r.width > 0 && r.height > 0 && style.display !== 'none'
                                    && style.visibility !== 'hidden' && style.opacity !== '0'};
                        })''')
                        report = {'url': page.url, 'title': page.title(),
                                  'consent_action': consent_action, 'full_page': self.full_page,
                                  'scope': 'Main document only; visible is a heuristic, not a content classification.',
                                  'elements': elements}
                        (self.diagnostics / 'elements.json').write_text(
                            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                        page.screenshot(path=str(self.diagnostics / 'page.png'), full_page=self.full_page)
                        print(f'Diagnostic : {self.diagnostics.resolve()} (page.png et elements.json)')
                    # currentSrc includes the browser-selected srcset candidate.
                    # No context menu, keyboard shortcut or DevTools is required.
                    page.eval_on_selector_all('img', '''images => images.forEach(img => {
                        const source = img.currentSrc || img.getAttribute('data-src') || img.src;
                        if (source) { img.setAttribute('src', source); img.removeAttribute('data-src'); }
                    })''')
                    return {'status': response.status, 'url': page.url,
                            'mode': 'browser', 'html': page.content()}
                finally:
                    browser.close()
        except Error as error:
            raise ValueError(f'Erreur navigateur : {error}') from error


class ImageExtractor(HTMLParser):
    """Collect unique HTTP(S) img sources, resolving relative URLs."""
    def __init__(self, page_url):
        super().__init__(convert_charrefs=True)
        self.base_url = page_url
        self.base_seen = False
        self.sources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'base' and not self.base_seen and attrs.get('href'):
            self.base_url = urllib.parse.urljoin(self.base_url, attrs['href'])
            self.base_seen = True
        if tag == 'img':
            source = attrs.get('data-src') or attrs.get('src')
            if source:
                self.sources.append((source.strip(), attrs.get('alt', '')))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def images(self):
        result, seen = [], set()
        for source, alt in self.sources:
            url = urllib.parse.urldefrag(urllib.parse.urljoin(self.base_url, source))[0]
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme in ('http', 'https') and parsed.hostname and url not in seen:
                result.append({'url': url, 'alt': alt})
                seen.add(url)
        return result


def download_images(images, directory, timeout, delay=(3.0, 22.0), proxy=None):
    """Sequential downloads, bounded to 10 MB each; record individual errors."""
    directory.mkdir(parents=True, exist_ok=True)
    opener = make_opener(proxy)
    extensions = {'image/jpeg': '.jpg', 'image/png': '.png',
                  'image/webp': '.webp', 'image/gif': '.gif',
                  'image/avif': '.avif', 'image/svg+xml': '.svg'}
    for index, image in enumerate(images, 1):
        if index > 1:
            pause = sample_pause(delay)
            print(f'Pause : {pause:.1f} s')
            time.sleep(pause)
        try:
            request = urllib.request.Request(image['url'], headers={
                'User-Agent': 'ScrapeLearningDemo/1.0', 'Accept': 'image/*'})
            with opener.open(request, timeout=timeout) as response:
                mime = response.headers.get_content_type()
                if mime not in extensions:
                    raise ValueError(f'Unsupported image content type: {mime}')
                data = response.read(10_000_001)
                if not data or len(data) > 10_000_000:
                    raise ValueError('Empty image or image exceeds 10 MB')
            filename = directory / f'image-{index:03d}{extensions[mime]}'
            filename.write_bytes(data)
            image.update({'file': str(filename.resolve()), 'bytes': len(data), 'content_type': mime})
        except (ValueError, OSError, urllib.error.URLError) as error:
            image['error'] = 'Echec du telechargement via proxy (aucun repli direct).' if proxy else str(error)

def demo_pool():
    # Illustrative profiles, not current browser fingerprints.
    profiles = [
        BrowserProfile('firefox-linux', 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
                       'fr-FR,fr;q=0.9', 'Linux', (1440, 900), 'Europe/Paris'),
        BrowserProfile('firefox-windows', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0',
                       'en-GB,en;q=0.9', 'Windows', (1920, 1080), 'Europe/London')]
    # Documentation addresses only. These are not real proxies.
    proxies = [ProxyExit('fr-a', 'FR', '192.0.2.10:8080'),
               ProxyExit('fr-b', 'FR', '192.0.2.11:8080'),
               ProxyExit('de-a', 'DE', '198.51.100.10:8080')]
    return SessionPool(profiles, proxies)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--country', default='FR')
    parser.add_argument('--allowed-country', action='append')
    parser.add_argument('--sessions', type=int, default=3)
    parser.add_argument('--url', help='Fetch one HTML page and extract image URLs')
    parser.add_argument('--output', default='runs/images.json')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--download', action='store_true', help='Download discovered images')
    parser.add_argument('--limit', type=int, default=20, help='Maximum images per run')
    parser.add_argument('--browser', action='store_true', help='Render JavaScript with optional Playwright')
    parser.add_argument('--allow-direct', action='store_true', help='Explicitly allow a run without SCRAPE_PROXY_SERVER')
    parser.add_argument('--diagnostics', metavar='DIRECTORY', help='Save viewport screenshot and image/canvas inventory')
    parser.add_argument('--reject-cookies', action='store_true', help='Click an unambiguous Reject All cookie button')
    parser.add_argument('--full-page', action='store_true', help='Capture the full document in diagnostics')
    parser.add_argument('--wait-ms', type=int, default=2000, help='Browser render wait, 0 to 30000 ms')
    parser.add_argument('--delay-min', type=float, default=3.0, help='Minimum pause in seconds')
    parser.add_argument('--delay-max', type=float, default=22.0, help='Maximum pause in seconds')
    parser.add_argument('--scroll-steps', type=int, default=0, help='Optional browser scroll steps (0 to 30)')
    args = parser.parse_args()
    try:
        proxy = ProxyConfig.from_environment()
    except ValueError as error:
        parser.error(str(error))
    if args.url and proxy is None and not args.allow_direct:
        parser.error('Proxy requis : definir SCRAPE_PROXY_SERVER. Pour un test direct explicite uniquement, utiliser --allow-direct.')
    if args.diagnostics and not args.browser:
        parser.error('--diagnostics requires --browser')
    if args.reject_cookies and not args.browser:
        parser.error('--reject-cookies requires --browser')
    if args.full_page and not args.diagnostics:
        parser.error('--full-page requires --diagnostics')
    if not (math.isfinite(args.delay_min) and math.isfinite(args.delay_max)
            and 0 <= args.delay_min <= args.delay_max <= 60):
        parser.error('delays must satisfy 0 <= delay-min <= delay-max <= 60')
    if not 0 <= args.scroll_steps <= 30:
        parser.error('scroll-steps must be between 0 and 30')
    if args.scroll_steps and not args.browser:
        parser.error('--scroll-steps requires --browser')
    delay = (args.delay_min, args.delay_max)
    if not 1 <= args.limit <= 100:
        parser.error('limit must be between 1 and 100')
    if args.download and not args.url:
        parser.error('--download requires --url')
    if args.browser and not args.url:
        parser.error('--browser requires --url')
    if not 0 <= args.wait_ms <= 30000:
        parser.error('wait-ms must be between 0 and 30000')
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    if args.url:
        try:
            plan = RequestPlan(args.url, None, {})
            transport = BrowserTransport(args.timeout, args.wait_ms, args.scroll_steps, delay, args.diagnostics,
                                         args.reject_cookies, args.full_page, proxy) if args.browser else HttpTransport(args.timeout, proxy)
            response = transport.execute(plan)
            extractor = ImageExtractor(response['url'])
            extractor.feed(response.pop('html'))
            extractor.close()
            images = extractor.images()
            response['discovered'] = len(images)
            response['images'] = images[:args.limit]
            response['count'] = len(response['images'])
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            if args.download:
                download_images(response['images'], output.parent / (output.stem + '_files'), args.timeout, delay, proxy)
                response['downloaded'] = sum('file' in image for image in response['images'])
            output.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"HTTP {response['status']} | {response['count']} images | {output.resolve()}")
            if args.download:
                print(f"Telechargements : {response['downloaded']}/{response['count']}")
            if not response['images']:
                print('Aucune image HTML reconnue : verifier le rendu JavaScript ou les images CSS.')
            return
        except (ValueError, OSError, urllib.error.URLError) as error:
            if proxy:
                parser.exit(1, 'Erreur de navigation avec proxy configure ; aucun repli direct. Verifier le fournisseur et les identifiants.\n')
            parser.exit(1, f'Erreur HTTP/extraction : {error}\n')
    if not 1 <= args.sessions <= 100:
        parser.error('sessions must be between 1 and 100')
    pool = demo_pool()
    transport = SimulatedTransport(c.upper() for c in (args.allowed_country or ['FR']))
    try:
        for _ in range(args.sessions):
            session = pool.new_session(args.country)
            plan = RequestPlan('local-fixture:viewer', session, session.browser.headers())
            print(json.dumps(transport.execute(plan)))
    except ValueError as error:
        parser.error(str(error))

if __name__ == '__main__':
    main()
