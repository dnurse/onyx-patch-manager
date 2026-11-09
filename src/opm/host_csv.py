"""Translate inventory CSV to public, unverified OPM host profiles."""

import csv
import io
import json
import re
from itertools import chain

from .validation import validate_host


CSV_FIELDS = ("name", "address", "ssh_port", "group_name", "protected")
MAX_CSV_BYTES = 65536
MAX_CSV_HOSTS = 250
_HEADER_ALIASES = {
    "name": "name", "server_name": "name", "host_name": "hostname", "hostname": "hostname",
    "alias": "alias", "address": "address", "ip": "address", "ip_address": "address",
    "ipv4_address": "ipv4_address", "ipv6_address": "ipv6_address",
    "ssh_port": "ssh_port", "port": "ssh_port", "group_name": "group_name", "group": "group_name",
    "protected": "protected",
}
_IDENTITY_FIELDS = {"name", "hostname", "alias", "address", "ipv4_address", "ipv6_address"}
_SPREADSHEET_PREFIXES = ("=", "+", "-", "@", "'", "\t", "\r", "\n")


def csv_cell(value):
    """Prevent a spreadsheet from treating exported inventory as formulas."""
    value = str(value)
    return "'" + value if value.lstrip(" ").startswith(_SPREADSHEET_PREFIXES) else value


def _import_cell(value):
    if value.startswith("'") and value[1:].lstrip(" ").startswith(_SPREADSHEET_PREFIXES):
        value = value[1:]
    return value.strip()


def _header(value):
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    return _HEADER_ALIASES.get(key)


def _delimiter(text):
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        first_line = next((line for line in text.splitlines() if line.strip()), "")
        return max((",", ";", "\t"), key=first_line.count)


def parse_hosts_csv(text):
    """Return valid (line, profile) pairs and row errors without retaining unused columns."""
    text = text.removeprefix("\ufeff")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("CSV must be 64 KiB or smaller.")
    if "\x00" in text:
        raise ValueError("Use a UTF-8 CSV file without null bytes.")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(text), strict=True)
    rows = ((reader.line_num, row) for row in reader if any(cell.strip() for cell in row))
    hosts, errors = [], []
    try:
        first = next(rows, None)
        if first is None:
            raise ValueError("CSV has no server rows.")
        columns = [_header(cell) for cell in first[1]]
        if _IDENTITY_FIELDS.intersection(columns):
            known = [column for column in columns if column]
            if len(known) != len(set(known)):
                raise ValueError("CSV contains duplicate recognized columns.")
        elif len(columns) == 1:
            columns = ["hostname"]
            rows = chain((first,), rows)
        else:
            raise ValueError("Use a header with name, hostname, or an address column.")
        for count, (line, row) in enumerate(rows, start=1):
            if count > MAX_CSV_HOSTS:
                raise ValueError(f"Import up to {MAX_CSV_HOSTS} servers per file.")
            if len(row) != len(columns):
                errors.append((line, "The number of fields does not match the header."))
                continue
            values = {column: _import_cell(value) for column, value in zip(columns, row) if column}
            try:
                hostname = values.get("hostname", "")
                address = (values.get("address") or values.get("ipv4_address")
                           or values.get("ipv6_address") or hostname)
                name = values.get("name") or values.get("alias") or hostname or address
                protected = values.get("protected", "").lower()
                if protected not in ("", "0", "1", "false", "true", "no", "yes"):
                    raise ValueError("Protected must be yes or no (also accepts true/false or 1/0).")
                fields = validate_host({
                    "name": name, "address": address, "ssh_user": "opm",
                    "ssh_port": values.get("ssh_port") or 22,
                    "group_name": values.get("group_name") or "Linux VMs",
                    "protected": protected in ("1", "true", "yes"),
                })
                hosts.append((line, {field: fields[field] for field in CSV_FIELDS}))
            except ValueError as error:
                errors.append((line, str(error)))
    except csv.Error as error:
        raise ValueError(f"Invalid CSV near line {reader.line_num}. Check quotes and delimiters.") from error
    if not hosts and not errors:
        raise ValueError("CSV has no server rows.")
    # The controller's Unix socket has a 64 KiB reader limit. Leave room for its envelope.
    if len(json.dumps([profile for _, profile in hosts])) > 60000:
        raise ValueError("This inventory is too large for one import. Split it into smaller files.")
    return hosts, errors
