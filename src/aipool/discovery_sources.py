"""Bounded public-web discovery leads; leads never activate providers."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, replace
from itertools import islice
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib import parse, request
from xml.etree import ElementTree
from html.parser import HTMLParser


MAX_RESPONSE_BYTES = 1_000_000
REDDIT_TERMS_URL = "https://www.redditinc.com/policies/data-api-terms"
_HTTP_LINK = re.compile(r"https?://[^\s<>\"')]+")


def _web_url(value: str, field: str, *, optional: bool = False) -> str:
    if optional and not value:
        return ""
    parsed = parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not contain credentials")
    return value


@dataclass(frozen=True, slots=True)
class DiscoveryLead:
    """A sourced lead that requires later normalization and operator review."""

    title: str
    source_url: str
    summary: str = ""
    external_url: str = ""
    source_kind: str = "web"
    terms_url: str = ""
    transport_hint: str = ""
    discovered_at: float = 0.0
    hit_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title) > 512:
            raise ValueError("lead title must be non-empty and at most 512 characters")
        if not self.source_kind.strip():
            raise ValueError("lead source_kind is required")
        _web_url(self.source_url, "source_url")
        _web_url(self.external_url, "external_url", optional=True)
        _web_url(self.terms_url, "terms_url", optional=True)
        if len(self.summary) > 8_000:
            raise ValueError("lead summary is too large")
        if self.discovered_at < 0 or self.hit_count < 0:
            raise ValueError("lead timestamps and hit count cannot be negative")

    @property
    def lead_id(self) -> str:
        identity = "\x00".join((self.source_url, self.external_url, self.title.casefold()))
        return "lead_" + hashlib.sha256(identity.encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "lead_id": self.lead_id}


class DiscoverySource(Protocol):
    def collect(self) -> Iterable[DiscoveryLead]:
        """Return a bounded set of provenance-preserving leads."""


class LeadRegistry:
    def __init__(self, store: Any | None = None) -> None:
        self._store = store
        self._leads: dict[str, DiscoveryLead] = {}
        if store is not None:
            for row in store.discovery_lead_rows():
                lead = DiscoveryLead(
                    title=str(row["title"]), source_url=str(row["source_url"]),
                    summary=str(row["summary"]), external_url=str(row["external_url"]),
                    source_kind=str(row["source_kind"]), terms_url=str(row["terms_url"]),
                    transport_hint=str(row["transport_hint"]),
                    discovered_at=float(row["discovered_at"]), hit_count=int(row["hit_count"]),
                    first_seen=float(row["first_seen"]), last_seen=float(row["last_seen"]),
                )
                self._leads[lead.lead_id] = lead

    def add(self, lead: DiscoveryLead, *, now: float | None = None) -> DiscoveryLead:
        timestamp = time.time() if now is None else float(now)
        existing = self._leads.get(lead.lead_id)
        if existing is None:
            stored = replace(lead, hit_count=1, first_seen=timestamp, last_seen=timestamp)
        else:
            stored = replace(
                lead,
                hit_count=existing.hit_count + 1,
                first_seen=existing.first_seen,
                last_seen=timestamp,
            )
        self._leads[stored.lead_id] = stored
        if self._store is not None:
            self._store.save_discovery_lead(stored)
        return stored

    def all(self) -> tuple[DiscoveryLead, ...]:
        return tuple(self._leads.values())

    def get(self, lead_id: str) -> DiscoveryLead:
        try:
            return self._leads[lead_id]
        except KeyError as exc:
            raise KeyError(f"unknown discovery lead: {lead_id}") from exc


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    leads: tuple[DiscoveryLead, ...]
    errors: tuple[str, ...]


class DiscoveryRunner:
    """Run a small sequential source batch with explicit hard bounds."""

    def __init__(self, sources: Iterable[DiscoverySource], *, max_sources: int = 8, max_leads: int = 32) -> None:
        if not 1 <= max_sources <= 32 or not 1 <= max_leads <= 256:
            raise ValueError("discovery bounds are invalid")
        self.sources = tuple(islice(sources, max_sources))
        self.max_sources = max_sources
        self.max_leads = max_leads

    def run(self, registry: LeadRegistry | None = None) -> DiscoveryRun:
        found: dict[str, DiscoveryLead] = {}
        errors: list[str] = []
        for source in islice(self.sources, self.max_sources):
            try:
                for lead in islice(source.collect(), self.max_leads):
                    if not isinstance(lead, DiscoveryLead):
                        raise TypeError("discovery source returned a non-lead")
                    found.setdefault(lead.lead_id, lead)
                    if len(found) >= self.max_leads:
                        break
                if len(found) >= self.max_leads:
                    break
            except Exception as exc:  # isolate one source from the scheduled run
                errors.append(type(exc).__name__)
        leads = tuple(found.values())
        if registry is not None:
            leads = tuple(registry.add(lead) for lead in leads)
        return DiscoveryRun(leads, tuple(errors))


def _fetch_reddit_json(url: str) -> Mapping[str, Any]:
    req = request.Request(url, headers={"User-Agent": "aipool-discovery/0.1"})
    with request.urlopen(req, timeout=10.0) as response:  # nosec B310: URL is operator-selected search endpoint
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("discovery response exceeds size limit")
    decoded = json.loads(raw)
    if not isinstance(decoded, Mapping):
        raise ValueError("discovery response must be a JSON object")
    return decoded


def _fetch_bounded_bytes(url: str) -> bytes:
    _web_url(url, "discovery URL")
    req = request.Request(url, headers={"User-Agent": "aipool-discovery/0.1"})
    with request.urlopen(req, timeout=10.0) as response:  # nosec B310: operator-selected source URL
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("discovery response exceeds size limit")
    return raw


def _fetch_bounded_json(url: str) -> Mapping[str, Any]:
    decoded = json.loads(_fetch_bounded_bytes(url))
    if not isinstance(decoded, Mapping):
        raise ValueError("discovery response must be a JSON object")
    return decoded


def _fetch_bounded_json_payload(url: str) -> Any:
    return json.loads(_fetch_bounded_bytes(url))


class RedditSearchSource:
    """Bounded public Reddit search lead source; it does not activate providers."""

    def __init__(
        self,
        query: str,
        *,
        subreddit: str | None = None,
        max_results: int = 10,
        fetch: Callable[[str], Mapping[str, Any]] = _fetch_reddit_json,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not query.strip():
            raise ValueError("Reddit search query is required")
        if not 1 <= max_results <= 25:
            raise ValueError("Reddit search max_results must be between 1 and 25")
        self.query = query
        self.subreddit = subreddit.strip() if subreddit else None
        self.max_results = max_results
        self.fetch = fetch
        self.clock = clock

    def collect(self) -> tuple[DiscoveryLead, ...]:
        params = {"q": self.query, "sort": "new", "limit": str(self.max_results), "raw_json": "1"}
        if self.subreddit:
            params["restrict_sr"] = "1"
            path = f"https://www.reddit.com/r/{parse.quote(self.subreddit, safe='')}/search.json"
        else:
            path = "https://www.reddit.com/search.json"
        payload = self.fetch(path + "?" + parse.urlencode(params))
        data = payload.get("data")
        children = data.get("children") if isinstance(data, Mapping) else None
        if not isinstance(children, list):
            raise ValueError("Reddit response is missing children")
        leads: list[DiscoveryLead] = []
        for child in children[:self.max_results]:
            item = child.get("data") if isinstance(child, Mapping) else None
            if not isinstance(item, Mapping) or not str(item.get("title", "")).strip():
                continue
            permalink = str(item.get("permalink", ""))
            if not permalink:
                continue
            source_url = parse.urljoin("https://www.reddit.com", permalink)
            target = str(item.get("url", ""))
            target_host = parse.urlsplit(target).netloc.casefold()
            external_url = target if target.startswith(("http://", "https://")) and "reddit.com" not in target_host else ""
            leads.append(DiscoveryLead(
                title=str(item["title"])[:512], source_url=source_url,
                summary=str(item.get("selftext", ""))[:8_000], external_url=external_url,
                source_kind="reddit-search", terms_url=REDDIT_TERMS_URL,
                transport_hint="browser-chat" if external_url else "unknown",
                discovered_at=float(self.clock()),
            ))
        return tuple(leads)


class RedditThreadSource:
    """Extract external chatbot links from a bounded Reddit discussion."""

    def __init__(
        self,
        thread_url: str,
        *,
        fetch: Callable[[str], Any] = _fetch_bounded_json_payload,
        max_results: int = 25,
        max_comments: int = 50,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _web_url(thread_url, "Reddit thread URL")
        if "reddit.com" not in parse.urlsplit(thread_url).netloc.casefold():
            raise ValueError("Reddit thread URL must be hosted on reddit.com")
        if not 1 <= max_results <= 100 or not 1 <= max_comments <= 200:
            raise ValueError("Reddit thread bounds are invalid")
        self.thread_url = thread_url.rstrip("/")
        self.fetch = fetch
        self.max_results = max_results
        self.max_comments = max_comments
        self.clock = clock

    def collect(self) -> tuple[DiscoveryLead, ...]:
        payload = self.fetch(self.thread_url + ".json?raw_json=1")
        if not isinstance(payload, list) or len(payload) < 2:
            raise ValueError("Reddit thread response is invalid")
        comments_listing = payload[1]
        leads: list[DiscoveryLead] = []
        seen_urls: set[str] = set()
        visited = 0

        def visit(node: Any) -> None:
            nonlocal visited
            if visited >= self.max_comments or len(leads) >= self.max_results:
                return
            data = node.get("data") if isinstance(node, Mapping) else None
            if not isinstance(data, Mapping):
                return
            body = str(data.get("body", ""))
            permalink = str(data.get("permalink", ""))
            if body:
                visited += 1
                source_url = parse.urljoin("https://www.reddit.com", permalink) if permalink else self.thread_url
                for match in _HTTP_LINK.findall(body):
                    target = match.rstrip(".,!?;:")
                    host = parse.urlsplit(target).netloc.casefold()
                    if not host or "reddit.com" in host or target in seen_urls:
                        continue
                    seen_urls.add(target)
                    leads.append(DiscoveryLead(
                        title=f"Reddit recommendation: {target}", source_url=source_url,
                        summary=body[:8_000], external_url=target,
                        source_kind="reddit-thread", terms_url=REDDIT_TERMS_URL,
                        transport_hint="browser-chat", discovered_at=float(self.clock()),
                    ))
                    if len(leads) >= self.max_results:
                        return
            replies = data.get("replies")
            children = replies.get("data", {}).get("children", []) if isinstance(replies, Mapping) else []
            if isinstance(children, list):
                for child in children:
                    visit(child)
                    if visited >= self.max_comments or len(leads) >= self.max_results:
                        return

        data = comments_listing.get("data") if isinstance(comments_listing, Mapping) else None
        children = data.get("children", []) if isinstance(data, Mapping) else []
        if not isinstance(children, list):
            raise ValueError("Reddit thread comments are invalid")
        for child in children:
            visit(child)
            if visited >= self.max_comments or len(leads) >= self.max_results:
                break
        return tuple(leads)


class JsonDirectorySource:
    """Normalize a bounded operator-selected JSON directory into leads."""

    def __init__(
        self,
        url: str,
        *,
        fetch: Callable[[str], Mapping[str, Any]] = _fetch_bounded_json,
        max_results: int = 25,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _web_url(url, "directory URL")
        if not 1 <= max_results <= 100:
            raise ValueError("directory max_results must be between 1 and 100")
        self.url, self.fetch, self.max_results, self.clock = url, fetch, max_results, clock

    def collect(self) -> tuple[DiscoveryLead, ...]:
        payload = self.fetch(self.url)
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            raise ValueError("directory response is missing items")
        leads: list[DiscoveryLead] = []
        for item in items[:self.max_results]:
            if not isinstance(item, Mapping):
                continue
            target = str(item.get("url", ""))
            if not target:
                continue
            try:
                _web_url(target, "directory item URL")
                source_url = str(item.get("source_url", self.url))
                _web_url(source_url, "directory source URL")
            except ValueError:
                continue
            leads.append(DiscoveryLead(
                title=str(item.get("name", item.get("title", "")))[:512],
                source_url=source_url, summary=str(item.get("description", ""))[:8_000],
                external_url=target, source_kind="json-directory",
                terms_url=str(item.get("terms_url", "")),
                transport_hint=str(item.get("transport", "browser-chat")),
                discovered_at=float(self.clock()),
            ))
        return tuple(leads)


class RssDiscoverySource:
    """Read a bounded RSS/Atom-like feed as provenance-only discussion leads."""

    def __init__(
        self,
        url: str,
        *,
        fetch: Callable[[str], bytes] = _fetch_bounded_bytes,
        max_results: int = 25,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _web_url(url, "feed URL")
        if not 1 <= max_results <= 100:
            raise ValueError("feed max_results must be between 1 and 100")
        self.url, self.fetch, self.max_results, self.clock = url, fetch, max_results, clock

    def collect(self) -> tuple[DiscoveryLead, ...]:
        root = ElementTree.fromstring(self.fetch(self.url))
        items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
        leads: list[DiscoveryLead] = []
        for item in items[:self.max_results]:
            values: dict[str, str] = {}
            for child in item:
                name = child.tag.rsplit("}", 1)[-1]
                if name in {"title", "link", "description", "summary"}:
                    values.setdefault(name, (child.text or "").strip())
            title, source_url = values.get("title", ""), values.get("link", "")
            if not title or not source_url:
                continue
            try:
                _web_url(source_url, "feed item URL")
            except ValueError:
                continue
            leads.append(DiscoveryLead(
                title=title[:512], source_url=source_url,
                summary=values.get("description", values.get("summary", ""))[:8_000],
                source_kind="rss", discovered_at=float(self.clock()),
            ))
        return tuple(leads)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None
            self._text = []


class HtmlPageSource:
    """Extract a bounded set of external links from a public article page."""

    def __init__(
        self,
        url: str,
        *,
        fetch: Callable[[str], bytes] = _fetch_bounded_bytes,
        max_results: int = 25,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _web_url(url, "page URL")
        if not 1 <= max_results <= 100:
            raise ValueError("page max_results must be between 1 and 100")
        self.url, self.fetch, self.max_results, self.clock = url, fetch, max_results, clock

    def collect(self) -> tuple[DiscoveryLead, ...]:
        parser = _LinkParser()
        parser.feed(self.fetch(self.url).decode("utf-8", errors="replace"))
        page_host = parse.urlsplit(self.url).netloc.casefold()
        leads: list[DiscoveryLead] = []
        seen: set[str] = set()
        for href, text in parser.links:
            target = parse.urljoin(self.url, href)
            parsed = parse.urlsplit(target)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            normalized = target.split("#", 1)[0]
            if not normalized or normalized in seen or parsed.netloc.casefold() == page_host:
                continue
            seen.add(normalized)
            leads.append(DiscoveryLead(
                title=(text or parsed.netloc)[:512], source_url=self.url,
                external_url=normalized, source_kind="html-page",
                transport_hint="browser-chat", discovered_at=float(self.clock()),
            ))
            if len(leads) >= self.max_results:
                break
        return tuple(leads)
