"""Named-entity extraction (NER enrichment) for ingested documents.

Two extractors share one contract (:func:`extract_entities` -> list of
:class:`ExtractedEntity`):

* **heuristic** (always available, deterministic, offline, free) - segments the text at
  every boundary a proper noun cannot cross (line breaks, markdown/list markers, table
  cells, label colons, sentence terminators), then walks each segment once, preferring a
  longest-match against a curated gazetteer and otherwise taking runs of capitalised
  tokens with leading/trailing function words trimmed off.
* **llm** (opt-in via ``ENTITY_EXTRACTION_MODE``) - asks the org's completion model for
  strict JSON. It degrades to the heuristic whenever no billable provider is configured,
  the call fails or times out, or the reply will not parse, so CI and keyless
  deployments behave exactly as before.

``kind`` is a function of the NAME alone (gazetteer, suffix table, keyword, first-name
list), never of surrounding context. The ``entities`` unique index is
``(org_id, kind, normalized)``, so a name classified differently in two documents would
otherwise surface as duplicate rows; :func:`sync_document_entities` additionally pins an
existing entity's kind so even the LLM path cannot fork one name into several rows.

The module reads top to bottom: tokenisation and segmentation patterns, the trim and
generic-noun lexicons, the gazetteer, the heuristic extractor, then the optional LLM
extractor and the document-sync writer.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import AuthContext
from app.core.logging import get_logger
from app.models.entity import DocumentEntity, Entity
from app.models.enums import ConnectorPurpose, EntityKind, OrgRole, UsageKind
from app.services.llm import ChatMessage, complete, is_offline, resolver
from app.services.llm.pricing import completion_cost, is_billable_provider
from app.services.metering import record_usage

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[^\W_][\w&.'\-]*", re.UNICODE)
r"""Dots/hyphens/apostrophes stay INSIDE a token so "Next.js", "third-brain.ai" and
"Parkinson's" survive; segmentation below has already removed sentence punctuation.
``[^\W_]`` is "word character except underscore", so accented Latin, Cyrillic, Greek and
CJK all tokenise correctly instead of being split apart or dropped."""

_LINE_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)+")
"""Markup that prefixes a line without being part of any entity."""

_SEGMENT_SPLIT_RE = re.compile(
    r"(?:(?<![.!?;:|,])[.!?;:|,]+(?:\s+|$))"
    r"|(?:\s+[-–—]{1,2}\s+)"
    r"|[()\[\]{}<>\"“”]"
    r"|\s{2,}"
)
"""Hard boundaries WITHIN a line. A proper noun never spans one of these, so splitting
here is what stops a heading or form label gluing onto the next capitalised word
("Founder Profile" + "What is your title" -> "Founder Profile What").

The alternation covers, in order: sentence/clause terminators and table cells, dashed
asides, brackets/quotes (which kills "[ TO ANSWER ]" bleed), and the column gutters of
fixed-width text. The lookbehind anchors the first alternative to the START of a
punctuation run: without it a line of N consecutive commas costs O(N^2) backtracking (a
20k-char run blocked the ingest worker's event loop for seconds)."""

_CONNECTORS = {"of", "the", "and", "for", "&", "de", "van", "der", "la", "le"}

_ARTICLES = {"the", "a", "an"}
_QUESTION_WORDS = {
    "what",
    "how",
    "why",
    "who",
    "when",
    "where",
    "which",
    "whether",
    "whom",
    "whose",
}
_AUX_AND_VERBS = {
    "are",
    "is",
    "was",
    "were",
    "be",
    "been",
    "being",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "can",
    "could",
    "will",
    "would",
    "should",
    "shall",
    "may",
    "might",
    "must",
    "include",
    "includes",
    "including",
    "explain",
    "describe",
    "please",
    "walk",
    "led",
    "lead",
    "leads",
    "set",
    "get",
    "make",
    "made",
    "use",
    "used",
    "using",
    "see",
    "note",
    "add",
    "added",
    "list",
    "provide",
    "attach",
    "enter",
    "return",
    "returns",
    "said",
    "says",
    "wrote",
    "built",
    "ships",
    "shipped",
    "run",
    "runs",
    "based",
    "watch",
    "check",
    "ensure",
    "verify",
    "confirm",
    "review",
    "restart",
    "rotate",
}
_PLACEHOLDER_TOKENS = {"to", "answer", "todo", "tbd", "na", "n", "a", "none", "null", "yes", "no"}
_SENTENCE_ADVERBS = {
    "later",
    "however",
    "then",
    "meanwhile",
    "also",
    "finally",
    "additionally",
    "therefore",
    "thus",
    "moreover",
    "furthermore",
    "subsequently",
    "recently",
    "today",
    "yesterday",
    "tomorrow",
    "here",
    "there",
    "overall",
    "initially",
    "eventually",
    "currently",
    "notably",
    "importantly",
    "every",
    "each",
    "both",
    "our",
    "your",
    "their",
    "his",
    "her",
    "its",
    "we",
    "i",
    "you",
    "they",
    "it",
    "this",
    "that",
    "these",
    "those",
    "but",
    "and",
    "or",
    "if",
    "so",
    "because",
    "while",
    "after",
    "before",
    "during",
    "since",
    "unless",
    "until",
    "first",
    "second",
    "third",
    "next",
    "last",
    "new",
    "old",
    "more",
    "most",
    "less",
    "some",
    "any",
    "all",
    "one",
    "two",
}
_DAYS_MONTHS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
_TRIM_TOKENS = (
    _ARTICLES
    | _QUESTION_WORDS
    | _AUX_AND_VERBS
    | _PLACEHOLDER_TOKENS
    | _SENTENCE_ADVERBS
    | _DAYS_MONTHS
)
"""Trimmed from BOTH ends of a run: sentence-initial capitals and form/question words
that get capitalised and would otherwise fuse onto a real name. A run made only of these
is never an entity."""

_GENERIC_NOUNS = {
    "team",
    "teams",
    "company",
    "companies",
    "project",
    "projects",
    "product",
    "products",
    "document",
    "documents",
    "user",
    "users",
    "customer",
    "customers",
    "employee",
    "employees",
    "manager",
    "engineer",
    "engineering",
    "founder",
    "founders",
    "profile",
    "summary",
    "overview",
    "introduction",
    "background",
    "progress",
    "equity",
    "funding",
    "idea",
    "ideas",
    "url",
    "urls",
    "text",
    "email",
    "name",
    "title",
    "role",
    "roles",
    "status",
    "notes",
    "note",
    "policy",
    "policies",
    "process",
    "step",
    "steps",
    "section",
    "page",
    "pages",
    "example",
    "examples",
    "question",
    "questions",
    "answer",
    "answers",
    "detail",
    "details",
    "data",
    "information",
    "content",
    "system",
    "systems",
    "service",
    "services",
    "api",
    "apis",
    "key",
    "keys",
    "value",
    "values",
    "type",
    "types",
    "kind",
    "kinds",
    "week",
    "month",
    "year",
    "day",
    "days",
    "time",
    "times",
    "date",
    "dates",
    "hour",
    "hours",
    "attach",
    "accomplishments",
    "responsibilities",
    "requirements",
    "goals",
    "objectives",
    "designer",
    "developer",
    "architect",
    "analyst",
    "scientist",
    "director",
    "president",
    "officer",
    "lead",
    "head",
    "intern",
    "contractor",
    "staff",
    "senior",
    "junior",
    "principal",
    "org",
    "organization",
    "organisation",
    "queue",
    "stall",
    "native",
    "rest",
    "operations",
    "ops",
    "people",
    "finance",
    "legal",
    "marketing",
    "sales",
    "support",
    "recruiting",
    "ingestion",
    "retrieval",
    "deployment",
    "migration",
    "rollback",
    "onboarding",
    "offboarding",
    "incident",
    "runbook",
    "checklist",
    "architecture",
    "topology",
    "conventions",
    "backups",
    "backup",
}
"""Generic nouns that are frequently capitalised in headings but index as noise. The tail
of the set is role/title words - "Staff Engineer", "Product Designer" are jobs, not
entities."""

_ORG_SUFFIXES = {
    "inc",
    "inc.",
    "corp",
    "corp.",
    "corporation",
    "llc",
    "ltd",
    "ltd.",
    "gmbh",
    "co",
    "co.",
    "plc",
    "company",
    "technologies",
    "labs",
    "systems",
    "group",
    "holdings",
    "partners",
    "institute",
    "university",
    "foundation",
    "college",
    "association",
    "society",
    "consortium",
    "ventures",
    "capital",
    "bank",
    "hospital",
    "school",
}
_PROJECT_KEYWORDS = {"project", "initiative", "program", "programme", "operation", "epic"}
_PLACE_WORDS = {
    "street",
    "avenue",
    "road",
    "boulevard",
    "lane",
    "drive",
    "way",
    "park",
    "square",
    "plaza",
    "building",
    "tower",
    "campus",
    "hall",
    "airport",
    "station",
    "bridge",
    "county",
    "district",
    "province",
    "valley",
    "island",
    "beach",
    "harbor",
    "harbour",
}
"""A trailing place word makes the whole phrase a location ("Market Street", "Hyde
Park")."""


def _g(kind: EntityKind, names: str) -> dict[str, tuple[str, EntityKind]]:
    out: dict[str, tuple[str, EntityKind]] = {}
    for raw in names.split(","):
        display = raw.strip()
        if display:
            out[display.lower()] = (display, kind)
    return out


_GAZETTEER: dict[str, tuple[str, EntityKind]] = {
    **_g(
        EntityKind.ORG,
        "Anthropic,OpenAI,Google,Microsoft,Amazon,Apple,Meta,Netflix,Nvidia,Intel,IBM,"
        "Oracle,Salesforce,Adobe,Uber,Lyft,Airbnb,Shopify,Spotify,Slack,Zoom,Dropbox,"
        "Atlassian,GitHub,GitLab,Docker,Databricks,Snowflake,MongoDB,Elastic,Confluent,"
        "HashiCorp,Cloudflare,DigitalOcean,Heroku,Vercel,Netlify,Supabase,Twilio,"
        "Okta,Stripe,Dropbox,WeWork,Crunchy Data,Upstash,Expensify,"
        "Auth0,Datadog,Sentry,PagerDuty,New Relic,Y Combinator,Sequoia,Andreessen Horowitz,"
        "a16z,Accel,Benchmark,Greylock,Khosla Ventures,Founders Fund,Techstars,"
        "UC San Diego,UC Berkeley,Stanford,MIT,Harvard,Caltech,Carnegie Mellon,"
        "Georgia Tech,Cornell,Princeton,Yale,Oxford,Cambridge,Deloitte,McKinsey,Accenture,"
        "Goldman Sachs,JPMorgan,Stripe Inc,Talmo Lab,Glean,Notion Labs",
    ),
    **_g(
        EntityKind.PRODUCT,
        "Claude,Claude Code,Claude Desktop,ChatGPT,GPT-4,GPT-4o,Gemini,Google Gemini,"
        "Llama,Mistral,Copilot,GitHub Copilot,Cursor,Windsurf,Codex,Whisper,DALL-E,"
        "Notion,Notion AI,Confluence,Jira,Linear,Asana,Trello,Figma,Miro,Airtable,"
        "Salesforce CRM,HubSpot,Zendesk,Intercom,Plaid,PayPal,QuickBooks,"
        "Slack Connect,Google Drive,Google Docs,Google Workspace,Microsoft Teams,"
        "Postgres,PostgreSQL,MySQL,SQLite,Redis,pgvector,Qdrant,Pinecone,Weaviate,Milvus,"
        "Chroma,Elasticsearch,OpenSearch,Kafka,RabbitMQ,Celery,arq,Airflow,dbt,Spark,"
        "Kubernetes,Terraform,Ansible,Nginx,Caddy,Envoy,Prometheus,Grafana,Loki,Jaeger,"
        "Memorystore,Cloud SQL,Cloud Run,CloudWatch,GCS,"
        "OpenTelemetry,Sentry SDK,FastAPI,Django,Flask,Starlette,Uvicorn,Gunicorn,"
        "SQLAlchemy,Alembic,Pydantic,pytest,Playwright,Cypress,Vitest,Ruff,mypy,"
        "httpx,structlog,requests,aiohttp,asyncio,Tenacity,Faker,Locust,"
        "npm,pnpm,Yarn,Vite,Webpack,Babel,ESLint,Prettier,"
        "React,Next.js,Vue,Svelte,Angular,Remix,Astro,Tailwind,Tailwind CSS,shadcn,"
        "TanStack Query,Redux,Zustand,Recharts,D3,Three.js,Node.js,Deno,"
        "TypeScript,JavaScript,Python,Rust,Golang,Java,Kotlin,Swift,Ruby,Elixir,"
        "PHP,Scala,Haskell,C++,Terraform Cloud,AWS,GCP,Azure,S3,EC2,Lambda,CloudFront,"
        "BigQuery,Snowflake Cloud,Looker,Tableau,Metabase,Superset,Azure OpenAI,"
        "PyTorch,TensorFlow,JAX,Keras,scikit-learn,pandas,NumPy,SciPy,Hugging Face,"
        "LangChain,LlamaIndex,Ollama,vLLM,SLEAP,MCP,Model Context Protocol,"
        "Third Brain,Docker Compose,GitHub Actions,Postman,Swagger,OpenAPI,GraphQL,gRPC",
    ),
    **_g(
        EntityKind.LOCATION,
        "San Francisco,San Diego,San Ramon,Los Angeles,New York,New York City,Seattle,"
        "Austin,Boston,Chicago,Denver,Portland,Atlanta,Miami,Dallas,Houston,Phoenix,"
        "Palo Alto,Mountain View,Menlo Park,Cupertino,Sunnyvale,Santa Clara,San Jose,"
        "Oakland,Berkeley,Pasadena,Irvine,Sacramento,La Jolla,Silicon Valley,Bay Area,"
        "London,Paris,Berlin,Munich,Amsterdam,Dublin,Madrid,Barcelona,Lisbon,Zurich,"
        "Stockholm,Copenhagen,Oslo,Helsinki,Warsaw,Prague,Vienna,Rome,Milan,"
        "Tokyo,Osaka,Seoul,Beijing,Shanghai,Shenzhen,Hong Kong,Singapore,Taipei,"
        "Bangalore,Bengaluru,Mumbai,Delhi,Hyderabad,Chennai,Pune,"
        "Toronto,Vancouver,Montreal,Ottawa,Ontario,Quebec,Mexico City,Sao Paulo,"
        "Buenos Aires,"
        "Sydney,Melbourne,Auckland,Tel Aviv,Dubai,Cape Town,Nairobi,Lagos,"
        "California,Texas,Washington,Oregon,Nevada,Arizona,Colorado,Florida,Georgia,"
        "Illinois,Massachusetts,Michigan,Minnesota,Ohio,Pennsylvania,Virginia,"
        "United States,USA,Canada,Mexico,Brazil,Argentina,United Kingdom,Ireland,France,"
        "Germany,Spain,Italy,Netherlands,Sweden,Norway,Denmark,Finland,Poland,Portugal,"
        "Switzerland,Austria,Israel,India,China,Japan,Korea,South Korea,Australia,"
        "New Zealand,Singapore City,Nigeria,Kenya,South Africa,Europe,Asia,Africa",
    ),
}
"""Name -> kind. Matched case-insensitively as a longest n-gram, so a single-token name
("Anthropic", "pgvector") is admitted with high confidence while a bare capitalised word
at a sentence start is not."""

_GAZETTEER_MAX_TOKENS = max(len(k.split()) for k in _GAZETTEER)
"""Longest gazetteer entry in tokens, so the scanner knows how far to look ahead."""

_FIRST_NAMES = {
    name.strip().lower()
    for name in [
        "James",
        "Robert",
        "John",
        "Michael",
        "David",
        "William",
        "Richard",
        "Joseph",
        "Thomas",
        "Charles",
        "Christopher",
        "Daniel",
        "Matthew",
        "Anthony",
        "Mark",
        "Donald",
        "Steven",
        "Paul",
        "Andrew",
        "Joshua",
        "Kenneth",
        "Kevin",
        "Brian",
        "George",
        "Timothy",
        "Ronald",
        "Edward",
        "Jason",
        "Jeffrey",
        "Ryan",
        "Jacob",
        "Gary",
        "Nicholas",
        "Eric",
        "Jonathan",
        "Stephen",
        "Larry",
        "Justin",
        "Scott",
        "Brandon",
        "Benjamin",
        "Samuel",
        "Gregory",
        "Alexander",
        "Patrick",
        "Frank",
        "Raymond",
        "Jack",
        "Dennis",
        "Jerry",
        "Tyler",
        "Aaron",
        "Jose",
        "Adam",
        "Nathan",
        "Henry",
        "Zachary",
        "Douglas",
        "Peter",
        "Kyle",
        "Noah",
        "Ethan",
        "Jeremy",
        "Christian",
        "Walter",
        "Keith",
        "Austin",
        "Roger",
        "Terry",
        "Sean",
        "Gerald",
        "Carl",
        "Dylan",
        "Harold",
        "Jordan",
        "Jesse",
        "Bryan",
        "Lawrence",
        "Arthur",
        "Gabriel",
        "Bruce",
        "Logan",
        "Billy",
        "Joe",
        "Alan",
        "Juan",
        "Elijah",
        "Willie",
        "Albert",
        "Wayne",
        "Randy",
        "Mason",
        "Vincent",
        "Liam",
        "Roy",
        "Bobby",
        "Caleb",
        "Bradley",
        "Russell",
        "Lucas",
        "Mary",
        "Patricia",
        "Jennifer",
        "Linda",
        "Elizabeth",
        "Barbara",
        "Susan",
        "Jessica",
        "Sarah",
        "Karen",
        "Nancy",
        "Lisa",
        "Margaret",
        "Betty",
        "Sandra",
        "Ashley",
        "Dorothy",
        "Kimberly",
        "Emily",
        "Donna",
        "Michelle",
        "Carol",
        "Amanda",
        "Melissa",
        "Deborah",
        "Stephanie",
        "Rebecca",
        "Laura",
        "Sharon",
        "Cynthia",
        "Kathleen",
        "Amy",
        "Shirley",
        "Angela",
        "Helen",
        "Anna",
        "Brenda",
        "Pamela",
        "Nicole",
        "Samantha",
        "Katherine",
        "Emma",
        "Ruth",
        "Christine",
        "Catherine",
        "Debra",
        "Rachel",
        "Carolyn",
        "Janet",
        "Virginia",
        "Maria",
        "Heather",
        "Diane",
        "Julie",
        "Joyce",
        "Victoria",
        "Kelly",
        "Christina",
        "Joan",
        "Evelyn",
        "Lauren",
        "Judith",
        "Olivia",
        "Frances",
        "Martha",
        "Cheryl",
        "Megan",
        "Andrea",
        "Hannah",
        "Jacqueline",
        "Ann",
        "Jean",
        "Alice",
        "Kathryn",
        "Gloria",
        "Teresa",
        "Doris",
        "Sara",
        "Janice",
        "Julia",
        "Marie",
        "Madison",
        "Grace",
        "Judy",
        "Theresa",
        "Beverly",
        "Denise",
        "Marilyn",
        "Amber",
        "Danielle",
        "Abigail",
        "Brittany",
        "Rose",
        "Diana",
        "Natalie",
        "Sophia",
        "Alexis",
        "Lori",
        "Kayla",
        "Jane",
        "Ketan",
        "Priya",
        "Rahul",
        "Amit",
        "Anita",
        "Arjun",
        "Deepak",
        "Divya",
        "Neha",
        "Nikhil",
        "Pooja",
        "Rajesh",
        "Ravi",
        "Sanjay",
        "Shreya",
        "Sunil",
        "Vikram",
        "Wei",
        "Li",
        "Chen",
        "Ming",
        "Yan",
        "Jing",
        "Hiroshi",
        "Yuki",
        "Kenji",
        "Takeshi",
        "Ahmed",
        "Mohammed",
        "Ali",
        "Fatima",
        "Omar",
        "Youssef",
        "Carlos",
        "Luis",
        "Miguel",
        "Sofia",
        "Isabella",
        "Mateo",
        "Diego",
        "Lucia",
        "Pierre",
        "Marie-Claire",
        "Hans",
        "Klaus",
        "Ingrid",
        "Lars",
        "Erik",
        "Anders",
        "Olga",
        "Ivan",
        "Dmitri",
        "Natasha",
        "Alina",
        "Bob",
        "Bill",
        "Tom",
        "Dave",
        "Mike",
        "Steve",
        "Chris",
        "Matt",
        "Nick",
        "Sam",
        "Alex",
        "Max",
        "Ben",
        "Dan",
        "Jim",
        "Ted",
        "Ron",
        "Ken",
        "Rob",
        "Tim",
        "Pat",
        "Kim",
        "Sue",
        "Liz",
        "Beth",
    ]
}
"""First names give PERSON a real signal instead of "any two capitalised words"."""

_MAX_ENTITIES = 50
_MAX_NAME_TOKENS = 6
_MAX_NAME_CHARS = 120

_ACRONYM_RE = re.compile(r"^[A-Z]{2,6}$")
"""Multi-cap acronyms worth indexing on their own; anything else all-caps is noise."""

_INTERNAL_CAPS_RE = re.compile(r"^[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*$")
"""Internal capitalisation ("FastAPI", "TypeScript", "PyTorch", "TanStack") is strong
evidence of a product/brand name even without a gazetteer hit."""


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    kind: EntityKind
    count: int


def normalize_name(name: str) -> str:
    """The dedup/upsert key for an entity name.

    Used by BOTH the extractor's in-document counter and
    :func:`sync_document_entities`' upsert, so the two can never diverge and split one
    entity into several rows.
    """
    cleaned = " ".join(name.split()).strip(" .,;:'\"")
    return cleaned.lower()


def _segments(text: str) -> list[str]:
    """Split ``text`` into fragments no proper noun may span."""
    out: list[str] = []
    for line in text.splitlines():
        line = _LINE_PREFIX_RE.sub("", line)
        if not line.strip():
            continue
        out.extend(part for part in _SEGMENT_SPLIT_RE.split(line) if part and part.strip())
    return out


def _is_capitalised(token: str) -> bool:
    return token[:1].isupper()


def _trim(run: list[str]) -> list[str]:
    """Drop function words from both ends of a candidate run."""
    while run and run[0].lower().strip(".") in _TRIM_TOKENS:
        run = run[1:]
    while run and run[-1].lower().strip(".") in _TRIM_TOKENS | _CONNECTORS:
        run = run[:-1]
    return run


def _single_token_admissible(token: str) -> bool:
    """Whether a lone token is strong enough evidence to index on its own.

    Only acronyms the gazetteer already knows are admitted; "TO" and "USA" are handled by
    the gazetteer/trim lists instead.
    """
    bare = token.strip(".")
    if len(bare) < 2:
        return False
    if _INTERNAL_CAPS_RE.match(bare):
        return True
    if _ACRONYM_RE.match(bare):
        return bare.lower() in _GAZETTEER
    return False


_FILE_SUFFIX_RE = re.compile(r"\.(?:md|py|ts|tsx|js|jsx|json|ya?ml|toml|txt|csv|sql|sh|ini)$", re.I)


def _is_constant_run(tokens: list[str]) -> bool:
    """Whether the capitalised tokens are all SCREAMING_CASE.

    Env-var names and enum listings from technical docs ("ORG TEAM PRIVATE",
    "OTEL_EXPORTER_OTLP_ENDPOINT", "JSON-RPC POST") are constants, not entities.
    """
    caps = [re.sub(r"[^A-Za-z]", "", t) for t in tokens if _is_capitalised(t)]
    caps = [c for c in caps if len(c) > 1]
    if any(c.lower() in _GAZETTEER for c in caps):
        return False
    return bool(caps) and all(c.isupper() for c in caps)


def _looks_like_person_token(token: str) -> bool:
    """A plausible given/family name token: alphabetic, Capitalised, not an acronym."""
    bare = token.strip(".")
    return (
        len(bare) > 1
        and bare.isalpha()
        and bare[0].isupper()
        and not bare.isupper()
        and bare.lower() not in _GENERIC_NOUNS
        and bare.lower() not in _TRIM_TOKENS
        and bare.lower() not in _ORG_SUFFIXES
        and bare.lower() not in _PLACE_WORDS
        and bare.lower() not in _GAZETTEER
    )


def _classify(tokens: list[str], normalized: str) -> EntityKind:
    """Map a name to its kind using name-intrinsic signals only (see module docstring).

    With no lexicon hit, exactly two clean name-shaped words is the classic "First Last".
    Requiring BOTH tokens to be alphabetic, non-acronym and absent from every lexicon is
    what keeps "Company URL" / "Staff Engineer" out of the person bucket.
    """
    if normalized in _GAZETTEER:
        return _GAZETTEER[normalized][1]
    last = tokens[-1].lower().strip(".")
    first = tokens[0].lower().strip(".")
    if last in _ORG_SUFFIXES:
        return EntityKind.ORG
    if first in _PROJECT_KEYWORDS:
        return EntityKind.PROJECT
    if last in _PLACE_WORDS:
        return EntityKind.LOCATION
    if len(tokens) >= 2 and first in _FIRST_NAMES and all(_is_capitalised(t) for t in tokens):
        return EntityKind.PERSON
    if len(tokens) == 2 and all(_looks_like_person_token(t) for t in tokens):
        return EntityKind.PERSON
    if _INTERNAL_CAPS_RE.match(tokens[0].strip(".")) and len(tokens) == 1:
        return EntityKind.PRODUCT
    return EntityKind.OTHER


def _match_gazetteer(tokens: list[str], i: int) -> tuple[str, EntityKind, int] | None:
    """Longest gazetteer match starting at ``tokens[i]``, or ``None``.

    Case must agree with the canonical form: a capitalised entry ("Redis") matches only a
    capitalised occurrence, a deliberately lowercase one ("pgvector") only a lowercase
    one. Without this, every gazetteer entry that is also an ordinary English word turns
    normal prose into entities ("we go to the square" -> Go, Square).
    """
    for size in range(min(_GAZETTEER_MAX_TOKENS, len(tokens) - i), 0, -1):
        window = [t.strip(".") for t in tokens[i : i + size]]
        hit = _GAZETTEER.get(" ".join(window).lower())
        if hit is None:
            continue
        canonical = hit[0]
        if canonical[:1].islower():
            if not window[0][:1].islower():
                continue
        elif not window[0][:1].isupper():
            continue
        return canonical, hit[1], size
    return None


def extract_entities(text: str, *, max_chars: int = 20000) -> list[ExtractedEntity]:
    """Extract and classify named entities from ``text`` (deterministic, offline).

    One pass per segment: a longest gazetteer match wins where it applies, otherwise a
    run of capitalised tokens is taken, trimmed of function words, and admitted only
    with real evidence (multi-word proper noun, org suffix, or a strong single token).
    That run is the maximal one, allowing lowercase connectors between two capitals
    ("Bank of America") and stopping at the first gazetteer hit so a known name is never
    swallowed into a longer run.
    """
    if not text:
        return []
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    kinds: dict[str, EntityKind] = {}

    def record(name: str, kind: EntityKind) -> None:
        norm = normalize_name(name)
        if not norm or len(name) > _MAX_NAME_CHARS:
            return
        words = norm.split()
        if not words or len(words) > _MAX_NAME_TOKENS:
            return
        if all(w in _TRIM_TOKENS | _GENERIC_NOUNS | _CONNECTORS for w in words):
            return
        counts[norm] += 1
        if norm not in display:
            display[norm] = name
            kinds[norm] = kind

    for segment in _segments(text[:max_chars]):
        tokens = _TOKEN_RE.findall(segment)
        i = 0
        while i < len(tokens):
            hit = _match_gazetteer(tokens, i)
            if hit is not None:
                name, kind, size = hit
                record(name, kind)
                i += size
                continue

            if not _is_capitalised(tokens[i]):
                i += 1
                continue

            start = i
            j = i
            while j < len(tokens):
                if j > start and _match_gazetteer(tokens, j) is not None:
                    break
                if _is_capitalised(tokens[j]) or (
                    tokens[j].lower() in _CONNECTORS
                    and j + 1 < len(tokens)
                    and _is_capitalised(tokens[j + 1])
                ):
                    j += 1
                else:
                    break
            run = _trim(tokens[start:j])
            i = max(j, start + 1)
            if not run:
                continue
            cap_count = sum(1 for t in run if _is_capitalised(t))
            if len(run) == 1:
                if not _single_token_admissible(run[0]):
                    continue
            elif cap_count < 2 and run[-1].lower().strip(".") not in _ORG_SUFFIXES:
                continue
            if any(_FILE_SUFFIX_RE.search(t) for t in run):
                continue
            if _is_constant_run(run) and normalize_name(" ".join(run)) not in _GAZETTEER:
                continue
            phrase = " ".join(run).strip(" .,'")
            if phrase:
                record(phrase, _classify(run, normalize_name(phrase)))

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_ENTITIES]
    return [
        ExtractedEntity(name=display[norm], kind=kinds[norm], count=count) for norm, count in ranked
    ]


_LLM_TIMEOUT_SECONDS = 30.0
_LLM_MAX_TOKENS = 2048
_LLM_MAX_RESPONSE_CHARS = 200_000
_LLM_MAX_ITEMS = 1000
_MAX_JSON_SCAN_RESTARTS = 8
_MAX_COUNT = 100_000
"""Counts are advisory; clamp so a hostile reply cannot overflow the integer column."""

_LLM_SYSTEM_PROMPT = """\
You extract named entities from one document of a company knowledge base and return \
them as strict JSON.

Return ONLY this object - no prose, no explanation, no markdown:
{"entities":[{"name":"...","kind":"...","count":1}]}

"kind" MUST be exactly one of:
  person   - a named human being ("Ketan Mittal").
  org      - a company, university, team or institution ("Anthropic", "Y Combinator").
  product  - a named software product, library, framework, service or model \
("FastAPI", "Postgres", "pgvector", "Claude Code", "Stripe").
  project  - a named internal initiative, workstream or codename ("Project Aurora").
  location - a city, region, country or campus ("San Diego", "San Francisco").
  other    - a proper noun that is clearly a named thing but fits none of the above.

Rules:
- Return the CANONICAL name only. Never include a question word, form label, heading \
fragment, verb or placeholder ("Founder Profile What", "TO ANSWER", "Third Brain \
Describe" are all WRONG; "Third Brain" is right).
- Only real named entities. Skip generic nouns ("the team", "our product"), section \
headings, dates and numbers.
- "count" is roughly how many times the entity appears.
- At most 60 entities, most important first. If the document names nothing, return \
{"entities":[]}.\
"""

_LLM_USER_TEMPLATE = """\
Document title: {title}

<document>
{text}
</document>

Return the JSON object now.\
"""

_VALID_KINDS = {k.value: k for k in EntityKind}


def _first_json_blob(text: str) -> str | None:
    """The first balanced ``{...}``/``[...]`` in ``text``, ignoring braces inside strings.

    Handles code fences, leading/trailing prose and a false brace before the real object.
    """
    search_from = 0
    for _ in range(_MAX_JSON_SCAN_RESTARTS):
        starts = [p for p in (text.find("{", search_from), text.find("[", search_from)) if p != -1]
        if not starts:
            return None
        start = min(starts)
        opener = text[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for pos in range(start, len(text)):
            ch = text[pos]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start : pos + 1]
        search_from = start + 1
    return None


def parse_llm_entities(text: str) -> list[ExtractedEntity]:
    """Parse a model reply into entities. Pure, never raises; ``[]`` means unusable."""
    if not text or not text.strip() or len(text) > _LLM_MAX_RESPONSE_CHARS:
        return []
    blob = _first_json_blob(text)
    if blob is None:
        return []
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return []

    if isinstance(data, dict):
        items = data.get("entities")
        if isinstance(items, dict):
            items = [items]
    else:
        items = data
    if not isinstance(items, list):
        return []

    seen: dict[str, ExtractedEntity] = {}
    for item in items[:_LLM_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        if not isinstance(raw_name, str):
            continue
        name = " ".join(raw_name.split()).strip(" .,;:'\"")
        norm = normalize_name(name)
        if not norm or len(name) > _MAX_NAME_CHARS or len(norm.split()) > _MAX_NAME_TOKENS:
            continue
        if all(w in _TRIM_TOKENS | _GENERIC_NOUNS for w in norm.split()):
            continue
        kind = _VALID_KINDS.get(str(item.get("kind", "")).strip().lower(), EntityKind.OTHER)
        try:
            count = min(_MAX_COUNT, max(1, int(item.get("count", 1))))
        except (TypeError, ValueError, OverflowError):
            count = 1
        if norm in seen:
            continue
        seen[norm] = ExtractedEntity(name=name, kind=kind, count=count)
        if len(seen) >= _MAX_ENTITIES:
            break
    return list(seen.values())


async def extract_entities_llm(
    db: AsyncSession, org_id: uuid.UUID, text: str, *, title: str | None = None
) -> list[ExtractedEntity] | None:
    """Ask the org's completion model for entities; ``None`` means "fall back".

    Returns ``None`` (never raises, except on cancellation) when no billable provider is
    configured, the call fails or times out, or the reply cannot be parsed - so the
    caller uses the deterministic heuristic instead.

    The provider is gated BEFORE the call so keyless deployments and CI never build a
    prompt at all, and again afterwards: :func:`complete` silently substitutes the
    offline stub on a provider error, and parsing its prose would yield nonsense. Real
    provider spend is metered on its own session so it survives a later rollback.
    """
    body = text[: settings.ENTITY_EXTRACTION_MAX_CHARS].strip()
    if not body:
        return None
    res = await resolver.resolve(db, org_id, ConnectorPurpose.COMPLETION)
    if is_offline(res.api_key, res.api_base, res.provider, purpose="completion"):
        return None

    prompt = _LLM_USER_TEMPLATE.format(title=title or "(untitled)", text=body)
    try:
        result = await asyncio.wait_for(
            complete(
                [
                    ChatMessage(role="system", content=_LLM_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=prompt),
                ],
                res.model,
                api_key=res.api_key,
                api_base=res.api_base,
                provider=res.provider,
                temperature=0.0,
                max_tokens=_LLM_MAX_TOKENS,
            ),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("entity_llm_call_failed", error=type(exc).__name__, detail=str(exc)[:200])
        return None

    if not is_billable_provider(result.provider):
        return None

    entities = parse_llm_entities(result.text)
    if not entities:
        logger.info("entity_llm_unparsable", provider=result.provider, model=result.model)
        return None

    async with SessionLocal() as usage_db:
        await record_usage(
            usage_db,
            AuthContext(org_id=org_id, org_role=OrgRole.ADMIN),
            UsageKind.COMPLETION,
            provider=result.provider,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            units=1,
            cost_usd=completion_cost(result.model, result.tokens_in, result.tokens_out),
            latency_ms=result.latency_ms,
            meta={"op": "entity_extraction"},
        )
        await usage_db.commit()
    return entities


async def reap_orphan_entities(db: AsyncSession, scope) -> None:
    """Delete entities matching ``scope`` that have no document links left.

    Deliberately two statements: ``SELECT ... FOR UPDATE`` blocks on any concurrent writer
    holding those rows, and the follow-up ``DELETE`` re-evaluates the link check under a
    fresh snapshot, so links committed while we waited protect their entity. The
    single-statement form evaluates the check on a stale snapshot and cascades away links
    another transaction just committed.
    """
    locked = (
        (
            await db.execute(
                select(Entity.id)
                .where(
                    scope,
                    ~select(DocumentEntity.id)
                    .where(DocumentEntity.entity_id == Entity.id)
                    .exists(),
                )
                .order_by(Entity.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not locked:
        return
    await db.execute(
        delete(Entity).where(
            Entity.id.in_(locked),
            ~select(DocumentEntity.id).where(DocumentEntity.entity_id == Entity.id).exists(),
        )
    )


async def sync_document_entities(
    db: AsyncSession,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    content: str,
    *,
    title: str | None = None,
) -> int:
    """Extract entities from ``content`` and (re)link them to ``document_id``.

    Reprocess-safe: replaces the document's existing links, upserts org-scoped ``Entity``
    rows, recomputes ``mention_count`` for every entity whose link set changed, and
    deletes entities left with no links at all (so re-extracting with a better algorithm
    clears the old noise instead of leaving it in the index forever with a zero count).
    The caller commits. Returns the number of entities linked.

    The unique index is ``(org_id, kind, normalized)``, so a name classified differently
    in another document would fork into a second visible row. The kind already on record
    for each name is therefore pinned - in ONE query rather than a probe per entity.
    Sorting by name gives every writer the same lock order, so two concurrent syncs
    cannot deadlock.

    Each entity is upserted with a self-resolving statement because that LOCKS the row it
    returns, so a concurrent orphan sweep cannot delete the entity between the upsert and
    the link insert below it (which would raise a foreign-key violation, or silently drop
    the link).
    """
    extracted: list[ExtractedEntity] | None = None
    if settings.ENTITY_EXTRACTION_MODE == "llm":
        extracted = await extract_entities_llm(db, org_id, content, title=title)
    if extracted is None:
        extracted = extract_entities(content, max_chars=settings.ENTITY_EXTRACTION_MAX_CHARS)

    previous = set(
        (
            await db.execute(
                select(DocumentEntity.entity_id).where(DocumentEntity.document_id == document_id)
            )
        )
        .scalars()
        .all()
    )
    await db.execute(delete(DocumentEntity).where(DocumentEntity.document_id == document_id))

    extracted = sorted(extracted, key=lambda e: normalize_name(e.name))
    normalized_names = [normalize_name(ent.name) for ent in extracted]
    pinned: dict[str, EntityKind] = {}
    if normalized_names:
        rows = await db.execute(
            select(Entity.normalized, Entity.kind).where(
                Entity.org_id == org_id, Entity.normalized.in_(normalized_names)
            )
        )
        pinned = dict(rows.all())

    touched: set[uuid.UUID] = set(previous)
    for ent, normalized in zip(extracted, normalized_names, strict=True):
        kind = pinned.get(normalized, ent.kind)
        entity_id = (
            await db.execute(
                pg_insert(Entity)
                .values(
                    org_id=org_id,
                    kind=kind,
                    name=ent.name,
                    normalized=normalized,
                    mention_count=0,
                )
                .on_conflict_do_update(
                    index_elements=["org_id", "kind", "normalized"], set_={"name": ent.name}
                )
                .returning(Entity.id)
            )
        ).scalar_one()
        await db.execute(
            pg_insert(DocumentEntity)
            .values(org_id=org_id, document_id=document_id, entity_id=entity_id, count=ent.count)
            .on_conflict_do_update(
                index_elements=["document_id", "entity_id"], set_={"count": ent.count}
            )
        )
        touched.add(entity_id)

    if touched:
        link_count = (
            select(func.count())
            .select_from(DocumentEntity)
            .where(DocumentEntity.entity_id == Entity.id)
            .scalar_subquery()
        )
        await db.execute(
            update(Entity).where(Entity.id.in_(touched)).values(mention_count=link_count)
        )
        await reap_orphan_entities(db, Entity.id.in_(touched))
    return len(extracted)
