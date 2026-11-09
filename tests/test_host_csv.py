import csv
import io
from concurrent.futures import ThreadPoolExecutor

import pytest

from opm.host_csv import CSV_FIELDS, MAX_CSV_HOSTS, csv_cell, parse_hosts_csv
from opm.store import Store


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_checkmk_columns_and_hostname_fallback(delimiter):
    text = delimiter.join(("Host name", "IPv4 address", "alias", "SNMP community")) + "\n"
    text += delimiter.join(("host1.example.test", "", "Web Server", "never-import-me")) + "\n"
    rows, errors = parse_hosts_csv("\ufeff" + text)
    assert errors == []
    assert rows == [(2, {
        "name": "Web Server", "address": "host1.example.test", "ssh_port": 22,
        "group_name": "Linux VMs", "protected": False,
    })]


def test_native_csv_round_trip_and_spreadsheet_formula_protection():
    profile = ("Router VM", "2001:db8::1", 2222, '=SUM(1,2)', True)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_FIELDS)
    writer.writerow(csv_cell(value) for value in profile)
    assert "'=SUM(1,2)" in output.getvalue()
    rows, errors = parse_hosts_csv(output.getvalue())
    assert not errors
    assert rows == [(2, dict(zip(CSV_FIELDS, profile)))]


def test_plain_host_list_and_invalid_rows():
    hosts, errors = parse_hosts_csv("host1.example.test\n\nhost2.example.test\n")
    assert [host["address"] for _, host in hosts] == ["host1.example.test", "host2.example.test"]
    assert not errors
    hosts, errors = parse_hosts_csv("name,address,ssh_port\nGood,192.0.2.1,22\nBad,192.0.2.2,0")
    assert len(hosts) == len(errors) == 1
    assert errors[0][0] == 3
    with pytest.raises(ValueError, match="up to"):
        parse_hosts_csv("\n".join(f"host{i}.example.test" for i in range(MAX_CSV_HOSTS + 1)))
    with pytest.raises(ValueError, match="Invalid CSV"):
        parse_hosts_csv('name,address\n"unclosed')


def test_preview_duplicates_and_import_leave_existing_profile_unchanged(tmp_path):
    store = Store(tmp_path / "state.db")
    old = store.add_host(dict(name="Existing", address="2001:db8::1", ssh_user="admin",
                              key_path="/private/key", protected=True))
    store.verified(old["id"])
    rows, errors = parse_hosts_csv(
        "name,address,ssh_port\nexisting,192.0.2.1,22\n"
        "Duplicate,2001:0db8::1,22\nNew Host,192.0.2.3,2222\nNew Alias,192.0.2.3,2222\n"
    )
    assert not errors
    batch = [host for _, host in rows]
    expected = ["Name already listed", "Address and SSH port already listed", "",
                "Address and SSH port already listed"]
    assert store.import_hosts(batch, preview=True) == expected
    assert len(store.hosts()) == 1
    assert store.import_hosts(batch) == expected
    current = store.host(old["id"])
    assert current["verified"] and current["protected"] and current["key_path"] == "/private/key"
    new = next(host for host in store.hosts() if host["name"] == "New Host")
    assert not new["verified"] and new["ssh_user"] == "opm" and not new["key_path"]
    assert all("key_path" not in host for host in store.host_csv_page(""))


def test_concurrent_imports_add_each_endpoint_once(tmp_path):
    store = Store(tmp_path / "state.db")
    batches = [[{"name": name, "address": "192.0.2.4"}] for name in ("First", "Second")]
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(store.import_hosts, batches))
    assert len(store.hosts()) == 1
    assert sorted(results) == [[""], ["Address and SSH port already listed"]]
