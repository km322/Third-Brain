"""Text extraction from raw document bytes and remote URLs.

Supports PDF (pypdf), DOCX (python-docx), HTML (BeautifulSoup), Markdown, plain
text and CSV. Format is detected from the MIME type first, then the filename
extension, then a light content sniff. Every heavy dependency is imported lazily
inside its branch so this module imports cleanly even when optional libraries are
absent, and any single extractor failing degrades gracefully to a UTF-8 decode.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import mimetypes
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.core.logging import get_logger

logger = get_logger(__name__)

# Cap remote downloads so a hostile/huge URL cannot exhaust memory.
MAX_URL_BYTES = 25 * 1024 * 1024
URL_FETCH_TIMEOUT_SECONDS = 30.0
# Hard wall-clock cap on the whole fetch (redirect loop + streaming). The httpx per-operation
# timeout resets on every received chunk, so a slow-loris peer dribbling one byte per interval
# can hold the connection open forever; this deadline bounds the total time regardless.
URL_FETCH_TOTAL_DEADLINE_SECONDS = 60
MAX_URL_REDIRECTS = 5

# Decompression-bomb guards: a small PDF/DOCX can inflate to gigabytes of text and OOM the
# worker. Cap the extracted output and, for zip-based formats, the declared uncompressed
# size before parsing.
MAX_EXTRACTED_CHARS = 20 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 5000


@dataclass
class _PinnedTarget:
    """A URL validated for SSRF and pinned to a concrete public IP.

    ``url`` has the hostname replaced by the resolved IP so the HTTP client connects to
    exactly that address; ``host_header`` and ``sni_hostname`` carry the original hostname
    so the right virtual host is reached and TLS still validates against the certificate.
    """

    url: str
    host_header: str
    sni_hostname: str


def _guard_address(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Return the address ``is_global`` should be evaluated against for SSRF checks.

    An IPv6 address that embeds an IPv4 address - IPv4-mapped (``::ffff:127.0.0.1``), 6to4
    or Teredo - actually routes to that inner IPv4 address, but ``is_global`` on CPython
    patch levels before 3.11.10 / 3.12.4 reports the outer IPv6 form as global. That let a
    mapped loopback/private target slip past the guard. Unwrap to the embedded IPv4 address
    so the public-address test matches the destination the connection really reaches; any
    other address is returned unchanged.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        teredo = ip.teredo
        if teredo is not None:
            return teredo[1]
    return ip


async def _resolve_public_target(url: str) -> _PinnedTarget:
    """Validate ``url`` for SSRF and pin it to a resolved public IP.

    URL ingestion accepts arbitrary user input, so a caller could otherwise make the
    server fetch cloud metadata (169.254.169.254), localhost, CGNAT or private-range
    internal services and read the response back. We resolve the host **once**, reject any
    non-global address, then connect to that exact IP. Pinning closes the DNS-rebinding
    TOCTOU: the previous approach validated the hostname and then let the HTTP client do a
    second, independent DNS lookup that an attacker-controlled resolver could rebind to an
    internal address between the two.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs may be ingested (got scheme {parts.scheme!r})")
    host = parts.hostname
    if not host:
        raise ValueError("URL has no host")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host {host!r}") from exc

    resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # ``is_global`` is the single authoritative "routable public address" test: it
        # rejects private, loopback, link-local, reserved, multicast, unspecified AND
        # carrier-grade NAT (100.64.0.0/10) in one check. Evaluate it against the embedded
        # IPv4 address for IPv4-mapped/6to4/Teredo IPv6 forms (see ``_guard_address``), so an
        # older interpreter cannot report a wrapped internal target as global. Reject if ANY
        # resolved address is non-public so a mixed answer cannot smuggle one through.
        if not _guard_address(ip).is_global:
            raise ValueError(f"Refusing to fetch a non-public address ({ip}) - SSRF protection")
        resolved.append(ip)
    if not resolved:
        raise ValueError(f"Could not resolve host {host!r}")

    pinned = resolved[0]
    literal = f"[{pinned}]" if pinned.version == 6 else str(pinned)
    pinned_netloc = f"{literal}:{parts.port}" if parts.port else literal
    pinned_url = urlunsplit((parts.scheme, pinned_netloc, parts.path, parts.query, parts.fragment))
    # Host header mirrors the URL authority minus any userinfo, so a non-default port is
    # preserved (host:port) exactly as a normal client would send it.
    host_header = parts.netloc.rsplit("@", 1)[-1]
    return _PinnedTarget(url=pinned_url, host_header=host_header, sni_hostname=host)


async def ensure_public_url(url: str) -> None:
    """Validate that ``url`` is an http(s) endpoint whose host resolves only to public IPs.

    The single, shared SSRF guard for any egress the server makes to a **caller-configured**
    endpoint (LLM/embedding connector base URLs, web-search base URLs). URL *ingestion*
    additionally pins per hop via :func:`_resolve_public_target`; this wrapper reuses the same
    resolution + ``is_global`` check for configuration-time validation, discarding the pinned
    result. Raises ``ValueError`` when the scheme is not http(s) or the host resolves to any
    non-global (loopback/link-local/private/CGNAT/reserved) address.
    """
    await _resolve_public_target(url)


@dataclass
class FetchResult:
    """The outcome of fetching a URL: raw bytes plus discovered metadata."""

    content: bytes
    mime_type: str | None = None
    title: str | None = None


def _decode(data: bytes) -> str:
    """Best-effort UTF-8 decode that never raises."""
    return data.decode("utf-8", errors="replace")


def _ext_of(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


# Structured formats, in match priority order: (extractor-key, MIME tokens, extensions).
_STRUCTURED_FORMATS: list[tuple[str, set[str], set[str]]] = [
    ("pdf", {"pdf"}, {"pdf"}),
    ("docx", {"officedocument.wordprocessingml", "msword"}, {"docx"}),
    # HTML by its own MIME tokens/extensions only. A bare "xml" substring also matches Office
    # Open XML containers (xlsx/pptx MIMEs contain "openxmlformats"), which would hand a
    # binary ZIP to the HTML parser and yield ~empty text.
    ("html", {"text/html", "application/xhtml"}, {"html", "htm"}),
    ("tsv", {"tab-separated"}, {"tsv"}),
    ("csv", {"csv"}, {"csv"}),
]

# MIME types we treat as authoritative plain text: a recognized text MIME must NOT be
# overridden by a misleading filename extension (e.g. a text/plain body titled "report.pdf").
_PLAIN_TEXT_MIME_PREFIXES = ("text/",)
_PLAIN_TEXT_MIME_TOKENS = {"markdown", "json", "yaml", "x-yaml", "javascript", "ecmascript"}


def _detect_format(mime: str, ext: str) -> str | None:
    """Pick an extractor key from MIME (authoritative) then filename extension.

    A recognized MIME type wins: only when the MIME is absent or unrecognized do we fall
    back to sniffing the filename extension. Returns ``None`` to mean "decode as plain text".
    """
    if mime:
        for name, mimes, _exts in _STRUCTURED_FORMATS:
            if any(token in mime for token in mimes):
                return name
        if mime.startswith(_PLAIN_TEXT_MIME_PREFIXES) or any(
            token in mime for token in _PLAIN_TEXT_MIME_TOKENS
        ):
            return None
        # An unrecognized MIME (e.g. application/octet-stream) is not authoritative; fall
        # through to the extension so a correctly-named upload still routes to its extractor.
    for name, _mimes, exts in _STRUCTURED_FORMATS:
        if ext in exts:
            return name
    return None


# Above this fraction of Unicode replacement characters, a "text" decode is really a binary
# blob (an image/zip/executable uploaded without a usable MIME): fail loudly instead of
# indexing mojibake.
_MAX_REPLACEMENT_CHAR_RATIO = 0.10


def _normalize_pdf_text(text: str) -> str:
    """Collapse pypdf spacing artifacts without disturbing real line structure.

    Google-Docs-style PDF exports come out of ``page.extract_text()`` with a
    lone-space line (``\\n \\n``) between soft-wrapped words and multi-space gaps
    between every word, which bloats chunks and degrades embedding quality. Real
    line breaks are single newlines and survive untouched.
    """
    text = text.replace("\n \n", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines())


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF has too many pages (>{MAX_PDF_PAGES})")
    pages: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # pragma: no cover - malformed page
            continue
        # Enforce the extracted-size cap INCREMENTALLY: a decompression-bomb PDF inflates a
        # small file into gigabytes of text, so abort as soon as the running total crosses the
        # limit instead of materializing every page and checking only after the join (the DOCX
        # path guards its declared uncompressed size up front; PDF has no such header, so this
        # running cap is the equivalent bound).
        total += len(text)
        if total > MAX_EXTRACTED_CHARS:
            raise ValueError(f"Extracted text exceeds {MAX_EXTRACTED_CHARS} character limit")
        if text.strip():
            pages.append(_normalize_pdf_text(text))
    return "\n\n".join(pages)


def _extract_docx(data: bytes) -> str:
    import zipfile

    import docx

    # A DOCX is a ZIP; reject a decompression bomb by its declared uncompressed size before
    # python-docx inflates it into memory.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            uncompressed = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid DOCX (bad ZIP container)") from exc
    if uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
        raise ValueError(f"DOCX uncompressed size exceeds {MAX_DOCX_UNCOMPRESSED_BYTES} byte limit")

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_html(data: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_csv(data: bytes, delimiter: str = ",") -> str:
    import csv

    text = _decode(data)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return "\n".join(", ".join(cell.strip() for cell in row) for row in rows if any(row))


def _html_title(data: bytes) -> str | None:
    try:
        from bs4 import BeautifulSoup, SoupStrainer

        # Parse only ``<title>`` so a large document does not build a full tree just to read
        # one tag.
        soup = BeautifulSoup(data, "html.parser", parse_only=SoupStrainer("title"))
        if soup.title and soup.title.string:
            return soup.title.string.strip()[:1024] or None
    except Exception:  # pragma: no cover
        pass
    return None


def extract_text(
    data: bytes,
    *,
    mime_type: str | None = None,
    filename: str | None = None,
) -> str:
    """Extract plain text from ``data`` based on its MIME type / filename.

    A recognized structured format (PDF/DOCX/HTML/CSV/TSV) whose dedicated extractor
    fails RAISES: those bytes are not text, so decoding them as UTF-8 would index mojibake
    while reporting success. Unrecognized types are decoded as UTF-8 (Markdown, plain
    text, and the like).
    """
    if not data:
        return ""
    mime = (mime_type or "").lower()
    ext = _ext_of(filename)

    fmt = _detect_format(mime, ext)
    if fmt == "pdf":
        text = _extract_pdf(data)
    elif fmt == "docx":
        text = _extract_docx(data)
    elif fmt == "html":
        text = _extract_html(data)
    elif fmt == "tsv":
        text = _extract_csv(data, delimiter="\t")
    elif fmt == "csv":
        text = _extract_csv(data)
    else:
        # Markdown, plain text and everything else: decode as UTF-8 text. If the result is
        # mostly replacement characters the bytes were really binary (an image/zip uploaded
        # without a usable MIME), so fail rather than index mojibake as if it succeeded.
        text = _decode(data)
        if text:
            replacements = text.count("�")
            if replacements / len(text) > _MAX_REPLACEMENT_CHAR_RATIO:
                raise ValueError("No extractable text found (content is not decodable text)")

    # Backstop against any format inflating to an unbounded amount of text.
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError(f"Extracted text exceeds {MAX_EXTRACTED_CHARS} character limit")
    return text


async def fetch_url(url: str) -> FetchResult:
    """Fetch ``url`` and return its bytes, resolved MIME type and (HTML) title.

    Raises ``ValueError`` on network errors, non-2xx responses or oversized bodies -
    the caller surfaces these as a 400 to the user.
    """
    import httpx

    # Redirects are followed MANUALLY so every hop is re-validated against SSRF
    # (httpx's auto-follow would bypass a check made only on the initial URL). Each hop is
    # resolved once and pinned to that IP, so the connection cannot rebind to an internal
    # address after the check.
    content = b""
    mime: str | None = None
    current = url
    try:
        # Overall deadline over the entire fetch (all redirect hops + streaming). httpx's
        # timeout is per-operation and resets on each received chunk, so it cannot bound a
        # slow peer that keeps trickling bytes; this is the hard wall-clock cap.
        async with asyncio.timeout(URL_FETCH_TOTAL_DEADLINE_SECONDS):
            async with httpx.AsyncClient(
                follow_redirects=False, timeout=URL_FETCH_TIMEOUT_SECONDS
            ) as client:
                for _hop in range(MAX_URL_REDIRECTS + 1):
                    target = await _resolve_public_target(current)
                    async with client.stream(
                        "GET",
                        target.url,
                        headers={
                            "Host": target.host_header,
                            "User-Agent": "ThirdBrain/1.0 (+ingestion)",
                        },
                        extensions={"sni_hostname": target.sni_hostname},
                    ) as resp:
                        if resp.is_redirect:
                            location = resp.headers.get("location")
                            if not location:
                                raise ValueError("Redirect response had no Location header")
                            current = str(httpx.URL(current).join(location))
                            continue
                        resp.raise_for_status()
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in resp.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_URL_BYTES:
                                raise ValueError(
                                    f"Remote document exceeds {MAX_URL_BYTES} byte limit"
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        mime = resp.headers.get("content-type", "").split(";")[0].strip() or None
                        break
                else:
                    raise ValueError("Too many redirects while fetching URL")
    except TimeoutError as exc:
        # Must precede the OSError handler: builtin TimeoutError subclasses OSError, so the
        # broad handler would otherwise swallow the deadline with the generic message.
        raise ValueError(
            f"Failed to fetch URL: exceeded {URL_FETCH_TOTAL_DEADLINE_SECONDS}s deadline"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"Failed to fetch URL: HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise ValueError(f"Failed to fetch URL: {exc}") from exc

    if mime is None:
        guessed, _ = mimetypes.guess_type(url)
        mime = guessed

    # Title extraction runs BeautifulSoup over up to MAX_URL_BYTES of HTML; keep that
    # synchronous parse off the request event loop.
    title = (await asyncio.to_thread(_html_title, content)) if (mime and "html" in mime) else None
    return FetchResult(content=content, mime_type=mime, title=title)
