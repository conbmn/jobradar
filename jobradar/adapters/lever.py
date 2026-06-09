import requests
from jobradar.adapters.base import ATSAdapter, Posting, to_plain_text

_API = "https://api.lever.co/v0/postings/{slug}?mode=json"


class LeverAdapter(ATSAdapter):
    def fetch(self, slug: str, with_content: bool = False) -> list[Posting]:
        resp = requests.get(_API.format(slug=slug), timeout=15)
        resp.raise_for_status()
        jobs = resp.json()
        postings = []
        for job in jobs:
            categories = job.get("categories") or {}
            # Lever's list payload already carries plain-text description fields.
            desc = job.get("descriptionPlain") or job.get("descriptionBodyPlain")
            postings.append(Posting(
                job_id=str(job["id"]),
                title=job.get("text", ""),
                location=categories.get("location", "") or "",
                url=job.get("hostedUrl", ""),
                description=to_plain_text(desc),
                raw=job,
            ))
        return postings
