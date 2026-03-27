"""
Drop professional / work skills that GPT sometimes puts in hobbies.

Uses whole-word and phrase matching so short tokens (e.g. hr, ai) do not
match unrelated words (thriller, sailing).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_PLACEHOLDER = frozenset({"", "<to be filled>", "to be filled", "n/a", "na", "-", "none", "tbd"})

# Multi-word or distinctive professional phrases (substring on normalized text).
_WORK_PHRASES: tuple[str, ...] = (
    "digital transformation",
    "employee engagement",
    "business development",
    "stakeholder management",
    "stakeholder relations",
    "stakeholder engagement",
    "project management",
    "product management",
    "program management",
    "portfolio management",
    "change management",
    "risk management",
    "crisis management",
    "talent acquisition",
    "talent management",
    "people management",
    "team management",
    "performance management",
    "organizational development",
    "human resources",
    "resource planning",
    "financial modeling",
    "financial analysis",
    "business analysis",
    "business analyst",
    "systems analysis",
    "swot analysis",
    "market analysis",
    "market research",
    "market expansion",
    "quality assurance",
    "quality management",
    "customer success",
    "customer experience",
    "sales enablement",
    "account management",
    "key account",
    "account executive",
    "business intelligence",
    "enterprise architecture",
    "solution architecture",
    "software architecture",
    "technical architecture",
    "systems architecture",
    "cloud computing",
    "cloud infrastructure",
    "machine learning",
    "deep learning",
    "data science",
    "data engineering",
    "data analytics",
    "predictive analytics",
    "artificial intelligence",
    "information security",
    "cyber security",
    "cybersecurity",
    "regulatory compliance",
    "risk compliance",
    "process improvement",
    "continuous improvement",
    "continuous integration",
    "continuous deployment",
    "process optimization",
    "business optimization",
    "operational excellence",
    "thought leadership",
    "strategic planning",
    "strategic initiative",
    "corporate strategy",
    "go-to-market",
    "go to market",
    "growth strategy",
    "revenue growth",
    "profit and loss",
    "p&l responsibility",
    "supply chain",
    "logistics optimization",
    "vendor management",
    "procurement management",
    "contract negotiation",
    "contract management",
    "budget management",
    "project delivery",
    "program delivery",
    "product delivery",
    "delivery leadership",
    "executive leadership",
    "cross-functional",
    "cross functional",
    "end-to-end",
    "end to end",
    "full stack",
    "full-stack",
    "fullstack",
    "software development",
    "software engineering",
    "web development",
    "app development",
    "java development",
    "java programming",
    "python programming",
    "python development",
    "python scripting",
    "javascript development",
    "typescript development",
    "application development",
    "systems engineering",
    "platform engineering",
    "site reliability",
    "business consulting",
    "management consulting",
    "it consulting",
    "digital marketing",
    "growth hacking",
    "seo optimization",
    "sem campaign",
    "content strategy",
    "brand strategy",
    "employer branding",
    "lean six sigma",
    "six sigma",
    "agile methodology",
    "scrum master",
    "product owner",
    "professional certification",
    "industry certification",
    "industry experience",
    "years of experience",
    "year experience",
    "work experience",
    "professional experience",
    "relevant experience",
    "domain expertise",
    "subject matter expert",
    "technical expertise",
    "technical skills",
    "core competencies",
    "key competencies",
    "professional skills",
    "interpersonal skills",
    "leadership skills",
    "management skills",
    "communication skills",
    "problem solving",
    "critical thinking",
    "analytical thinking",
    "stakeholder communication",
    "client facing",
    "client-facing",
    "b2b sales",
    "b2c marketing",
    "saas platform",
    "erp implementation",
    "crm implementation",
    "digital platform",
    "enterprise software",
)

# Whole-token matches only (word boundaries applied later).
_WORK_TOKENS: frozenset[str] = frozenset({
    # Agile / delivery
    "agile", "scrum", "kanban", "devops", "sprint", "backlog", "roadmap",
    "deliverable", "deliverables", "jira", "confluence",
    # Cloud / infra
    "kubernetes", "k8s", "docker", "terraform", "ansible", "jenkins",
    "microservices", "serverless", "multicloud", "devsecops",
    # Vendors / enterprise tools
    "salesforce", "workday", "servicenow", "sap", "oracle", "snowflake",
    "databricks", "tableau", "powerbi", "splunk", "datadog",
    # Languages / stacks (unambiguous in CV hobby context)
    "javascript", "typescript", "react", "angular", "vue",
    "node", "nodejs", "golang", "kotlin", "scala", "csharp", "dotnet",
    "ruby", "rails", "django", "flask", "springboot", "hibernate",
    "graphql", "mongodb", "postgresql", "mysql", "redis", "kafka", "rabbitmq",
    "elasticsearch", "nginx", "webpack",
    # Cloud brands
    "aws", "azure", "gcp",
    # Data / tech role shorthand
    "sql", "nosql", "etl", "elt", "dbt", "api", "rest", "grpc", "oauth",
    # Corporate / role
    "saas", "paas", "iaas", "erp", "crm", "hris", "kpi", "okr", "roi",
    "cio", "cto", "cfo", "ceo", "coo", "cmo", "cpo", "cro", "vp",
    "pmp", "cisa", "cissp", "cpa", "mba",
    # Soft-skills-as-hobby red flags (avoid bare tokens like coaching/networking)
    "stakeholder", "negotiation", "governance", "compliance", "procurement", "licensing",
    "outsourcing", "offshoring", "restructuring", "downsizing", "budgeting",
})

_WORK_HINT_RE = re.compile(
    r"|".join(
        (
            r"\b\d+\+?\s*y(?:ears?)?\s+(?:of\s+)?(?:experience|exp)\b",
            r"\b(?:senior|junior|lead|principal|staff)\s+(?:engineer|developer|consultant|manager|analyst|designer)\b",
            r"\b(?:software|data|cloud|solutions|technical|product|project|program)\s+(?:engineer|architect|manager|lead|director|consultant)\b",
            r"\b(?:certified|certification)\s+(?:in\s+)?(?:aws|azure|gcp|pmp|scrum|safe|itil|cissp|six\s+sigma)\b",
            r"\b(?:proficient|experienced|skilled)\s+in\b",
            r"\bhands-?on\s+(?:experience|expertise)\b",
        )
    ),
    re.IGNORECASE,
)


def _normalize_hobby_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[\u2010-\u2015\u2212]", "-", t)  # hyphen variants
    return t


def _tokenize_words(normalized: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalized))


def is_personal_hobby(item: str | None) -> bool:
    """True if the string looks like a personal hobby, not a work skill."""
    text = (item or "").strip()
    if not text:
        return False

    normalized = _normalize_hobby_text(text)
    if normalized in _PLACEHOLDER:
        return False

    for phrase in _WORK_PHRASES:
        if phrase in normalized:
            return False

    if _WORK_HINT_RE.search(text):
        return False

    words = _tokenize_words(normalized)
    if words & _WORK_TOKENS:
        return False

    return True


def filter_personal_hobbies(items: Iterable[str]) -> list[str]:
    return [h for h in items if is_personal_hobby(h)]
