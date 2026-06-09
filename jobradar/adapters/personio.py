import xml.etree.ElementTree as ET

import requests

from jobradar.adapters.base import ATSAdapter, Posting, to_plain_text

_API = "https://{slug}.jobs.personio.com/xml"
_JOB_URL = "https://{slug}.jobs.personio.com/job/{job_id}"


class PersonioAdapter(ATSAdapter):
    def fetch(self, slug: str, with_content: bool = False) -> list[Posting]:
        resp = requests.get(_API.format(slug=slug), timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        postings = []
        for pos in root.findall("position"):
            job_id = (pos.findtext("id") or "").strip()
            if not job_id:
                continue

            offices = [o.strip() for o in (
                [pos.findtext("office") or ""]
                + [el.text or "" for el in pos.findall("additionalOffices/office")]
            ) if o.strip()]

            # JD lives in jobDescriptions/jobDescription/value (HTML); concat sections.
            sections = [
                (el.findtext("value") or "")
                for el in pos.findall("jobDescriptions/jobDescription")
            ]
            description = to_plain_text(" ".join(s for s in sections if s))

            postings.append(Posting(
                job_id=job_id,
                title=(pos.findtext("name") or "").strip(),
                location=", ".join(offices),
                url=_JOB_URL.format(slug=slug, job_id=job_id),
                description=description,
            ))
        return postings
