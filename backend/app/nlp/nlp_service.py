"""NLP service for extracting entities from transcript text."""
from __future__ import annotations

import os
from typing import List, Set

import openai
from ..config import settings


def extract_organizations(text: str, max_length: int | None = None) -> List[str]:
    """
    Extract organization names from transcript text using OpenAI.

    Args:
        text: The transcript text to analyze
        max_length: Maximum characters to process (to avoid token limits)

    Returns:
        List of unique organization names found in the text
    """
    if not text or len(text.strip()) == 0:
        return []

    if max_length is None:
        max_length = settings.nlp_max_text_length

    # Truncate text if too long
    analysis_text = text[:max_length] if len(text) > max_length else text

    client = openai.OpenAI(api_key=settings.openai_api_key)

    system_prompt = """You are an expert at extracting organization names from earnings call transcripts.

Your task is to identify ALL organizations (companies, institutions, agencies) mentioned in the text.

Guidelines:
- Include company names, subsidiaries, partners, competitors, customers
- Include abbreviated forms (e.g., "the SEC", "the Fed")
- Include both full names and common abbreviations if both are mentioned
- Exclude generic terms like "the company", "management", "investors"
- Return ONLY the organization names, one per line
- Be comprehensive but avoid duplicates"""

    user_prompt = f"""Extract all organization names from this earnings call transcript:

{analysis_text}

List all organizations mentioned, one per line:"""

    try:
        response = client.chat.completions.create(
            model=settings.nlp_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=settings.nlp_temperature,
            max_tokens=settings.nlp_max_tokens
        )

        result = response.choices[0].message.content

        # Parse the response - should be one org per line
        organizations = []
        for line in result.split('\n'):
            org = line.strip()
            # Remove leading numbers, bullets, dashes
            org = org.lstrip('0123456789.-•* ')
            if org and len(org) > 1:
                organizations.append(org)

        # Remove duplicates while preserving order
        seen: Set[str] = set()
        unique_orgs = []
        for org in organizations:
            org_lower = org.lower()
            if org_lower not in seen:
                seen.add(org_lower)
                unique_orgs.append(org)

        return unique_orgs

    except Exception as e:
        print(f"Warning: Failed to extract organizations: {e}")
        return []
