"""
Send extracted CV text to OpenAI GPT-4o and get back structured JSON.
"""

import json
import os
import re

from openai import OpenAI
from pydantic import BaseModel, Field

from app.hobby_filter import filter_personal_hobbies


# ── Pydantic models for structured CV data ──────────────────

class SkillCategory(BaseModel):
    category: str
    skills: str

class ExperienceEntry(BaseModel):
    title: str
    company: str
    start_date: str
    end_date: str
    scope: str = ""
    bullets: list[str] = Field(default_factory=list)

class ProjectEntry(BaseModel):
    company: str
    project: str
    role: str = ""
    duration: str
    tools: str = ""
    bullets: list[str] = Field(default_factory=list)

class EducationEntry(BaseModel):
    degree: str
    institution: str
    year: str
    grade: str = ""

class LanguageEntry(BaseModel):
    language: str
    level: str

class CVData(BaseModel):
    name: str
    nationality: str = "<To be filled>"
    position_applied: str = "<To be filled>"
    total_experience: str = "<To be filled>"
    relevant_experience: str = "<To be filled>"
    location: str = "<To be filled>"
    notice_period: str = "<To be filled>"
    professional_summary: str = ""
    technical_skills: list[SkillCategory] = Field(default_factory=list)
    business_skills: list[SkillCategory] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    professional_experience: list[ExperienceEntry] = Field(default_factory=list)
    project_experience: list[ProjectEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    hobbies: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a CV data extractor. Given raw text from a CV/resume, extract ALL information and return it as structured JSON matching the schema below.

Rules:
- Extract every piece of information from the CV. Do not omit anything.
- For fields that cannot be found in the CV, use "<To be filled>" as the value.
- For total_experience and relevant_experience, express as "X years" format. Calculate total experience from the earliest to latest job dates if not explicitly stated.
- For technical_skills, group skills into logical categories (e.g., "Programming Languages", "Databases", "Frameworks", "Tools", etc.).
- For business_skills, extract any business/management/domain skills mentioned separately from technical skills. Leave empty array if none found.
- For professional_experience, extract each job as a separate entry with bullet points describing responsibilities/achievements.
- For project_experience, only include if the CV has a distinct projects section separate from work experience. Leave empty array if projects are embedded within work experience entries.
- For education, extract degree name, institution, graduation year, and grade/GPA if available.
- For education, include ONLY Diploma-level qualifications or higher (Diploma, Advanced Diploma, Bachelor's, Master's, PhD, etc.). Exclude high school, secondary school, matriculation, SPM/O-Level/A-Level, or equivalent lower qualifications.
- For languages, extract each language with proficiency level on a strict numeric scale from 1 to 10. Convert textual descriptors (e.g. Native/Fluent/Advanced/Intermediate/Basic) to the closest numeric value.
- For hobbies, extract ONLY personal, non-work interests (e.g. sports, music, travel, reading for pleasure, arts, volunteering causes). Do NOT list professional skills, technologies, methodologies, certifications, or work interests (e.g. digital transformation, stakeholder management, cloud, agile, SaaS, leadership, analytics). If the CV has no clear personal hobbies, use an empty array.
- The professional_summary should be a concise paragraph summarizing the candidate's profile. If the CV has an existing summary/objective, use it. If not, compose a brief one from the CV content.
- Dates should be in "Mon YYYY" format (e.g., "Jan 2020"). Use "Present" for current positions.

Return ONLY valid JSON matching the provided schema."""


def _is_diploma_or_higher(degree_name: str) -> bool:
    text = (degree_name or "").strip().lower()
    if not text:
        return False

    higher_keywords = {
        "diploma", "advanced diploma", "bachelor", "master", "mba",
        "phd", "doctorate", "postgraduate", "graduate", "associate degree",
        "degree", "bsc", "msc", "ba ", "bs ", "b.eng", "m.eng", "btech", "mtech",
    }
    lower_keywords = {
        "high school", "secondary", "primary", "matriculation", "spm",
        "o-level", "olevel", "a-level", "alevel", "gcse", "igcse", "stpm",
        "foundation", "pre-u", "pre university",
    }

    if any(k in text for k in lower_keywords):
        return False

    # Accept common bachelor/master abbreviations even with punctuation
    compact = re.sub(r"[^a-z0-9]", "", text)
    if compact.startswith(("ba", "bs", "bsc", "beng", "btech", "ma", "ms", "msc", "meng", "mtech")):
        return True

    return any(k in text for k in higher_keywords)


def _normalize_language_level(level: str) -> str:
    text = (level or "").strip().lower()
    if not text:
        return "5"

    direct_map = {
        "native": 10, "mother tongue": 10,
        "fluent": 9, "advanced": 8,
        "upper intermediate": 7, "intermediate": 6,
        "conversational": 5, "basic": 4, "beginner": 3,
        "elementary": 2,
    }
    if text in direct_map:
        return str(direct_map[text])

    # CEFR scale mapping
    cefr_map = {
        "a1": 2, "a2": 3,
        "b1": 5, "b2": 7,
        "c1": 9, "c2": 10,
    }
    if text in cefr_map:
        return str(cefr_map[text])

    # Parse ratio format first (e.g. "7/10", "8.5 / 10")
    ratio_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", text)
    if ratio_match:
        try:
            value = float(ratio_match.group(1))
            value = max(1.0, min(10.0, value))
            return str(int(round(value)))
        except ValueError:
            pass

    # Parse single numeric value (e.g. "7", "9.0")
    number_match = re.search(r"\d+(?:\.\d+)?", text)
    if number_match:
        try:
            value = float(number_match.group(0))
            value = max(1.0, min(10.0, value))
            return str(int(round(value)))
        except ValueError:
            pass

    return "5"


def parse_cv(raw_text: str) -> CVData:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract all CV data from the following text:\n\n{raw_text}"},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "cv_data",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "nationality": {"type": "string"},
                        "position_applied": {"type": "string"},
                        "total_experience": {"type": "string"},
                        "relevant_experience": {"type": "string"},
                        "location": {"type": "string"},
                        "notice_period": {"type": "string"},
                        "professional_summary": {"type": "string"},
                        "technical_skills": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string"},
                                    "skills": {"type": "string"},
                                },
                                "required": ["category", "skills"],
                                "additionalProperties": False,
                            },
                        },
                        "business_skills": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string"},
                                    "skills": {"type": "string"},
                                },
                                "required": ["category", "skills"],
                                "additionalProperties": False,
                            },
                        },
                        "soft_skills": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "certifications": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "awards": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "professional_experience": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "company": {"type": "string"},
                                    "start_date": {"type": "string"},
                                    "end_date": {"type": "string"},
                                    "scope": {"type": "string"},
                                    "bullets": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["title", "company", "start_date", "end_date", "scope", "bullets"],
                                "additionalProperties": False,
                            },
                        },
                        "project_experience": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "company": {"type": "string"},
                                    "project": {"type": "string"},
                                    "role": {"type": "string"},
                                    "duration": {"type": "string"},
                                    "tools": {"type": "string"},
                                    "bullets": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["company", "project", "role", "duration", "tools", "bullets"],
                                "additionalProperties": False,
                            },
                        },
                        "education": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "degree": {"type": "string"},
                                    "institution": {"type": "string"},
                                    "year": {"type": "string"},
                                    "grade": {"type": "string"},
                                },
                                "required": ["degree", "institution", "year", "grade"],
                                "additionalProperties": False,
                            },
                        },
                        "languages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "language": {"type": "string"},
                                    "level": {"type": "string"},
                                },
                                "required": ["language", "level"],
                                "additionalProperties": False,
                            },
                        },
                        "hobbies": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "name", "nationality", "position_applied",
                        "total_experience", "relevant_experience",
                        "location", "notice_period", "professional_summary",
                        "technical_skills", "business_skills", "soft_skills",
                        "certifications", "awards", "professional_experience",
                        "project_experience", "education", "languages", "hobbies",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        temperature=0.1,
    )

    raw_json = response.choices[0].message.content
    data = json.loads(raw_json)
    # Business rule: keep position applied as placeholder for manual fill.
    data["position_applied"] = "<To be filled>"
    # Business rule: keep relevant experience as placeholder for manual fill.
    data["relevant_experience"] = "<To be filled>"
    # Business rule: keep only Diploma-or-higher qualifications.
    data["education"] = [
        edu for edu in data.get("education", [])
        if _is_diploma_or_higher(edu.get("degree", ""))
    ]
    # Business rule: language levels must be on a 1-10 numeric scale.
    for lang in data.get("languages", []):
        lang["level"] = _normalize_language_level(lang.get("level", ""))
    # Drop items that look like work skills mis-tagged as hobbies.
    data["hobbies"] = filter_personal_hobbies(data.get("hobbies", []))
    return CVData(**data)
