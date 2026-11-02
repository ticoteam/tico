"""Library files on records (M4, X10e): a vendor's contract, an item's certificate of analysis, a piece of equipment's
manual and a location's file link to the record. A link is made under the records right, and only to a file the asker
can read (the Library check, equipment.file_readable); a viewer sees only the linked files they can read."""
import pytest

from backend.finance import equipment as EQ
from backend.finance import reads as FR
from backend.finance.core.errors import NOT_AVAILABLE, NOT_FOUND
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

COA = "lib-coa-gadget"          # a Library file everyone can read
PRIVATE = "lib-contract-acme"   # a Library file only the owner can read
EVE = "human:eve"


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(EQ, "file_readable",
                        lambda c, who, file_id: file_id == COA or (who == OWNER and file_id == PRIVATE))


def _made(c, action: str, payload: dict, key: str) -> str:
    done = act(c, OWNER, action, payload)
    assert done.status == "committed", done.reason
    return str(done.result[key])


def _link(c, subject_type: str, subject_id: str, file_id: str) -> str:
    done = act(c, OWNER, "record_file_add", {"subject_type": subject_type, "subject_id": subject_id,
                                             "file_id": file_id})
    assert done.status == "committed", done.reason
    return str(done.result["record_file_id"])


def _file_ids(files: list) -> set[str]:
    return {str(f["file_id"]) for f in files}


def test_m4_a_certificate_is_linked_once_and_shows_on_the_item(books, library):
    with books.transaction() as c:
        item = _made(c, "item_create", {"name": "Gadget", "kind": "service"}, "item_id")
        link = _link(c, "item", item, COA)
        assert _link(c, "item", item, COA) == link                       # the same file again is the same link
        assert FR.record(c, OWNER, kind="item", record_id=item, at=AT)["item"]["files"] == [
            {"id": link, "file_id": COA, "added_at": AT, "added_by_id": OWNER}]


def test_a_viewer_sees_only_the_linked_files_they_can_read(books, library):
    with books.transaction() as c:
        item = _made(c, "item_create", {"name": "Gadget", "kind": "service"}, "item_id")
        _link(c, "item", item, COA)
        _link(c, "item", item, PRIVATE)
        assert act(c, OWNER, "view_defaults_set", {"member": "none"}).status == "committed"
        assert _file_ids(FR.record(c, OWNER, kind="item", record_id=item, at=AT)["item"]["files"]) == {COA, PRIVATE}
        assert _file_ids(FR.record(c, EVE, kind="item", record_id=item, at=AT)["item"]["files"]) == {COA}


def test_a_vendors_contract_a_pieces_manual_and_a_locations_file_show_until_unlinked(books, library):
    with books.transaction() as c:
        vendor = _made(c, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}, "contact_id")
        unit = _made(c, "equipment_create", {"name": "Hand truck", "kind": "owned"}, "equipment_id")
        site = _made(c, "location_create", {"name": "Denver", "kind": "store"}, "location_id")
        contract = _link(c, "contact", vendor, PRIVATE)
        _link(c, "equipment", unit, COA)
        _link(c, "location", site, COA)
        assert _file_ids(FR.contact(c, OWNER, vendor, at=AT)["files"]) == {PRIVATE}
        assert _file_ids(FR.record(c, OWNER, kind="equipment", record_id=unit, at=AT)["equipment"]["files"]) == {COA}
        assert _file_ids(FR.record(c, OWNER, kind="location", record_id=site, at=AT)["location"]["files"]) == {COA}
        removed = act(c, OWNER, "record_file_remove", {"record_file_id": contract})
        assert removed.status == "committed", removed.reason
        assert FR.contact(c, OWNER, vendor, at=AT)["files"] == []
        assert act(c, OWNER, "record_file_remove", {"record_file_id": contract}).code == NOT_FOUND
        assert c.execute("SELECT COUNT(*) FROM record_files").fetchone()[0] == 2


def test_a_file_the_asker_cannot_read_or_a_missing_record_is_not_linked(books, library):
    with books.transaction() as c:
        item = _made(c, "item_create", {"name": "Gadget", "kind": "service"}, "item_id")
        unreadable = act(c, OWNER, "record_file_add", {"subject_type": "item", "subject_id": item,
                                                       "file_id": "lib-none"})
        assert unreadable.code == NOT_FOUND
        nothing = act(c, OWNER, "record_file_add", {"subject_type": "item", "subject_id": "no-such-item",
                                                    "file_id": COA})
        assert nothing.code == NOT_FOUND
        assert c.execute("SELECT COUNT(*) FROM record_files").fetchone()[0] == 0


def test_with_no_library_check_installed_no_file_is_linked_or_shown(books, monkeypatch):
    """X10e (fail closed): no check means no link and no shown file, never an unchecked one."""
    monkeypatch.setattr(EQ, "file_readable", None)
    with books.transaction() as c:
        item = _made(c, "item_create", {"name": "Gadget", "kind": "service"}, "item_id")
        refused = act(c, OWNER, "record_file_add", {"subject_type": "item", "subject_id": item, "file_id": COA})
        assert refused.code == NOT_AVAILABLE
        assert FR.record(c, OWNER, kind="item", record_id=item, at=AT)["item"]["files"] == []


def test_linking_a_file_needs_the_records_right(books, library):
    with books.transaction() as c:
        item = _made(c, "item_create", {"name": "Gadget", "kind": "service"}, "item_id")
        refused = act(c, "human:dan", "record_file_add", {"subject_type": "item", "subject_id": item,
                                                          "file_id": COA})
        assert refused.status == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        granted = act(c, "human:dan", "record_file_add", {"subject_type": "item", "subject_id": item, "file_id": COA})
        assert granted.status == "committed", granted.reason
