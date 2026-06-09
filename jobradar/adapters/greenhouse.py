import requests
from jobradar.adapters.base import ATSAdapter, Posting, to_plain_text

_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseAdapter(ATSAdapter):
    def fetch(self, slug: str, with_content: bool = False) -> list[Posting]:
        # The list endpoint omits job `content` unless asked — only pay for it during
        # a real scrape, not during detector slug-validation (with_content=False).
        url = _API.format(slug=slug) + ("?content=true" if with_content else "")
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        postings = []
        for job in jobs:
            loc_obj = job.get("location") or {}
            location = loc_obj.get("name", "") if isinstance(loc_obj, dict) else ""
            postings.append(Posting(
                job_id=str(job["id"]),
                title=job.get("title", ""),
                location=location,
                url=job.get("absolute_url", ""),
                description=to_plain_text(job.get("content")),  # entity-encoded HTML
                raw=job,
            ))
        return postings
