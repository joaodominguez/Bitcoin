from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import BinaryIO, Iterable, TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Contact, ContactList, ListMember, Suppression, utcnow

EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)

# Common header aliases (lowercased)
EMAIL_HEADERS = {"email", "e-mail", "mail", "endereco", "endereço", "correo"}
NAME_HEADERS = {"name", "nome", "full_name", "fullname", "contacto"}
TAGS_HEADERS = {"tags", "tag", "labels", "etiquetas"}
SOURCE_HEADERS = {"source", "origem", "origin", "lista"}
CONSENT_HEADERS = {"consent", "consentimento", "consent_note", "opt_in"}


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped_invalid: int = 0
    skipped_suppressed: int = 0
    skipped_duplicate_in_file: int = 0
    errors: list[str] = field(default_factory=list)
    total_rows: int = 0


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    email = value.strip().lower()
    if not email or not EMAIL_RE.match(email):
        return None
    if len(email) > 320:
        return None
    return email


def _pick(row: dict[str, str], aliases: set[str]) -> str | None:
    for key, val in row.items():
        if key in aliases and val and str(val).strip():
            return str(val).strip()
    return None


def _decode_stream(raw: BinaryIO) -> TextIO:
    data = raw.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    return io.StringIO(text)


def _detect_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        class Fallback(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL

        return Fallback()


def iter_contact_rows(raw: BinaryIO) -> Iterable[dict[str, str]]:
    stream = _decode_stream(raw)
    sample = stream.read(8192)
    stream.seek(0)
    dialect = _detect_dialect(sample)
    reader = csv.DictReader(stream, dialect=dialect)
    if not reader.fieldnames:
        return
    fieldnames = [(f or "").strip().lower() for f in reader.fieldnames]
    for row in reader:
        mapped = {}
        for key, val in row.items():
            if key is None:
                continue
            mapped[key.strip().lower()] = (val or "").strip()
        # If no recognized email header, try first column
        if not any(h in mapped for h in EMAIL_HEADERS) and fieldnames:
            first = fieldnames[0]
            if first and mapped.get(first):
                mapped["email"] = mapped[first]
        yield mapped


def import_contacts(
    session: Session,
    raw: BinaryIO,
    *,
    list_name: str | None = None,
    default_source: str | None = "csv",
    update_existing: bool = True,
) -> ImportResult:
    """Stream-import contacts from a CSV file handle."""
    result = ImportResult()
    contact_list = None
    if list_name:
        name = list_name.strip()
        contact_list = session.scalar(select(ContactList).where(ContactList.name == name))
        if contact_list is None:
            contact_list = ContactList(name=name, description="Criada no import CSV")
            session.add(contact_list)
            session.flush()

    suppressed = {
        e for e in session.scalars(select(Suppression.email)).all()
    }

    seen_in_file: set[str] = set()
    batch: list[Contact] = []
    BATCH = 500

    for row in iter_contact_rows(raw):
        result.total_rows += 1
        email = normalize_email(_pick(row, EMAIL_HEADERS) or row.get("email"))
        if not email:
            result.skipped_invalid += 1
            if len(result.errors) < 20:
                result.errors.append(f"Linha {result.total_rows}: email inválido")
            continue
        if email in seen_in_file:
            result.skipped_duplicate_in_file += 1
            continue
        seen_in_file.add(email)

        if email in suppressed:
            result.skipped_suppressed += 1
            continue

        name = _pick(row, NAME_HEADERS)
        tags = _pick(row, TAGS_HEADERS)
        source = _pick(row, SOURCE_HEADERS) or default_source
        consent = _pick(row, CONSENT_HEADERS)

        existing = session.scalar(select(Contact).where(Contact.email == email))
        if existing:
            if update_existing:
                if name:
                    existing.name = name
                if tags:
                    existing.tags = tags
                if source:
                    existing.source = source
                if consent:
                    existing.consent_note = consent
                existing.updated_at = utcnow()
                result.updated += 1
                contact = existing
            else:
                contact = existing
        else:
            contact = Contact(
                email=email,
                name=name,
                tags=tags,
                source=source,
                consent_note=consent,
                status="active",
            )
            session.add(contact)
            batch.append(contact)
            result.created += 1

        if contact_list is not None:
            session.flush()
            exists_member = session.scalar(
                select(ListMember.id).where(
                    ListMember.list_id == contact_list.id,
                    ListMember.contact_id == contact.id,
                )
            )
            if not exists_member:
                session.add(ListMember(list_id=contact_list.id, contact_id=contact.id))

        if len(batch) >= BATCH:
            session.flush()
            batch.clear()

    session.flush()
    return result
