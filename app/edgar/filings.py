"""Fetch a company's filings from EDGAR and filter by form + date window."""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

from app.config import settings
from app.edgar.client import EdgarClient, EdgarError
from app.models import Filing

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
HISTORY_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"

# Documents worth scanning for buyback language are narrative HTML/text. We skip
# XBRL statement renderings (R1.htm, R2.htm, ...), index/header pages, and the
# concatenated full-submission .txt (which would just duplicate everything).
_SCANNABLE_EXT = (".htm", ".html", ".txt")
_XBRL_RENDER_RE = re.compile(r"^R\d+\.htm$", re.IGNORECASE)


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    accession_nodashes = accession_number.replace("-", "")
    return ARCHIVES_URL.format(
        cik_int=int(cik),
        accession=accession_nodashes,
        doc=primary_document,
    )


def _filings_from_block(
    block: dict,
    *,
    cik: str,
    wanted_forms: set[str],
    cutoff: date,
) -> list[Filing]:
    """Turn one submissions column-block into Filing objects within the window.

    A "block" is the SEC's column-oriented filing table (parallel arrays for
    ``form``, ``filingDate``, ``accessionNumber``, ...). It is used both for the
    inline ``filings.recent`` table and for the paginated history files.
    """

    form_list = block.get("form", [])
    filing_dates = block.get("filingDate", [])
    report_dates = block.get("reportDate", [])
    accession_numbers = block.get("accessionNumber", [])
    primary_documents = block.get("primaryDocument", [])

    results: list[Filing] = []
    for i, form in enumerate(form_list):
        if form.upper() not in wanted_forms:
            continue
        filing_date = _parse_date(filing_dates[i]) if i < len(filing_dates) else None
        if filing_date is None or filing_date < cutoff:
            continue
        primary_document = (
            primary_documents[i] if i < len(primary_documents) else ""
        )
        if not primary_document:
            continue
        accession_number = accession_numbers[i]
        report_date = (
            _parse_date(report_dates[i]) if i < len(report_dates) else None
        )
        results.append(
            Filing(
                form=form,
                filing_date=filing_date,
                report_date=report_date,
                accession_number=accession_number,
                primary_document=primary_document,
                document_url=_build_document_url(
                    cik, accession_number, primary_document
                ),
            )
        )
    return results


def _history_files_in_window(payload: dict, cutoff: date) -> list[str]:
    """Names of paginated history submission files overlapping the window.

    For high-volume filers the inline ``recent`` table only covers ~the last
    year; older filings live in additional files listed under
    ``filings.files``. We need any file whose date range reaches ``cutoff`` or
    later (i.e. ``filingTo >= cutoff``).
    """

    files = payload.get("filings", {}).get("files", [])
    wanted: list[str] = []
    for entry in files:
        filing_to = _parse_date(entry.get("filingTo", ""))
        if filing_to is None or filing_to >= cutoff:
            wanted.append(entry["name"])
    return wanted


async def fetch_recent_filings(
    client: EdgarClient,
    cik: str,
    *,
    forms: tuple[str, ...] | None = None,
    lookback_days: int | None = None,
    today: date | None = None,
) -> list[Filing]:
    """Return filings of the requested forms filed within the lookback window.

    ``cik`` must be the zero-padded 10-digit CIK string.

    The SEC's submissions document only inlines roughly the most recent year of
    filings under ``filings.recent``; older filings are paginated into separate
    history files. When the lookback window extends past the inline table we
    fetch those history files too, so a multi-year ``lookback_days`` actually
    reaches that far back.
    """

    forms = forms or settings.forms
    if not isinstance(lookback_days, int):
        lookback_days = settings.lookback_days
    today = today or date.today()
    cutoff = today - timedelta(days=lookback_days)
    wanted_forms = {f.upper() for f in forms}

    payload = await client.get_json(SUBMISSIONS_URL.format(cik=cik))
    recent = payload.get("filings", {}).get("recent", {})

    results = _filings_from_block(
        recent, cik=cik, wanted_forms=wanted_forms, cutoff=cutoff
    )

    history_files = _history_files_in_window(payload, cutoff)
    if history_files:
        blocks = await asyncio.gather(
            *(
                client.get_json(HISTORY_URL.format(name=name))
                for name in history_files
            )
        )
        for block in blocks:
            results.extend(
                _filings_from_block(
                    block, cik=cik, wanted_forms=wanted_forms, cutoff=cutoff
                )
            )

    return results


def _is_scannable_document(name: str, accession_number: str) -> bool:
    """Whether a document in a filing directory is worth scanning for text."""

    lower = name.lower()
    if not lower.endswith(_SCANNABLE_EXT):
        return False
    if _XBRL_RENDER_RE.match(name):
        return False
    # Index / header pages are navigation, not content.
    if "index" in lower or lower.endswith("-index.htm"):
        return False
    # The complete submission is served as "<accession>.txt"; it concatenates
    # every document in the filing, so scanning it would duplicate the others.
    if lower == f"{accession_number.lower()}.txt":
        return False
    return True


async def fetch_filing_document_urls(
    client: EdgarClient, filing: Filing
) -> list[str]:
    """Return URLs of the narrative documents to scan for a filing.

    Buyback authorizations are frequently announced in an exhibit (e.g. an
    earnings press release attached as EX-99) rather than the primary document,
    so we scan every narrative document in the filing's directory. Falls back to
    just the primary document if the directory index can't be read.
    """

    directory = filing.document_url.rsplit("/", 1)[0]
    try:
        index = await client.get_json(f"{directory}/index.json")
    except EdgarError:
        return [filing.document_url]

    urls: list[str] = []
    for item in index.get("directory", {}).get("item", []):
        name = item.get("name", "")
        if _is_scannable_document(name, filing.accession_number):
            urls.append(f"{directory}/{name}")

    if filing.document_url not in urls:
        urls.append(filing.document_url)
    return urls
