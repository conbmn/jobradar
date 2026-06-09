import requests
from jobradar.adapters.base import ATSAdapter, Posting, to_plain_text

_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


class AshbyAdapter(ATSAdapter):
    def fetch(self, slug: str, with_content: bool = False) -> list[Posting]:
        resp = requests.get(_API.format(slug=slug), timeout=15)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        postings = []
        for job in jobs:
            # Only surface roles actually listed on the public board
            if job.get("isListed") is False:
                continue
            postings.append(Posting(
                job_id=str(job["id"]),
                title=job.get("title", ""),
                location=job.get("location", "") or "",
                url=job.get("jobUrl", "") or job.get("applyUrl", ""),
                description=to_plain_text(job.get("descriptionPlain")),  # already plain
                raw=job,
            ))
        return postings
