"""scripts/check_ui_strict.py: the strict ratchet (entry, growth, lowering, the old layout at a ref), the JavaScript rule and
the shift-left rules."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_ui_strict as strict  # noqa: E402
import ui_baseline  # noqa: E402

REF = "origin/main"


def finding(file, line=1):
    return strict.Finding(file, line, 1, "TS2339", "Property 'x' does not exist on type 'never'.")


@pytest.fixture
def renamed(monkeypatch):
    """The .ts paths that are renames of a .js file at REF; a test adds to the set."""
    names = set()
    monkeypatch.setattr(strict, "is_rename_of_js", lambda ref, rel: rel in names)
    return names


def test_a_rename_of_a_js_file_may_carry_its_count(renamed):
    renamed.add("ui/app/a.ts")
    assert strict.check_growth({"ui/app/a.ts": 4}, {}, REF) == []


def test_a_brand_new_ts_file_with_findings_is_refused(renamed):
    problems = strict.check_growth({"ui/app/new.ts": 1}, {}, REF)
    assert len(problems) == 1 and problems[0].startswith("NEW BASELINE ui/app/new.ts: 1 finding(s)")


def test_a_brand_new_ts_file_with_findings_is_not_strict_either():
    problems = strict.check_strict({"ui/app/new.ts": [finding("ui/app/new.ts")]}, {}, {"ui/app/new.ts"})
    assert problems == [
        "NOT STRICT ui/app/new.ts: 1 finding(s); a .ts file without a piece must pass",
        "    ui/app/new.ts:1:1: TS2339 Property 'x' does not exist on type 'never'.",
    ]


def test_a_count_rising_against_the_ref_is_refused(renamed):
    problems = strict.check_growth({"ui/app/a.ts": 3}, {"ui/app/a.ts": 2}, REF)
    assert problems == ["GREW ui/app/a.ts: 2 -> 3 since origin/main; fix the findings instead"]


def test_a_count_falling_without_write_is_refused():
    findings = {"ui/app/a.ts": [finding("ui/app/a.ts")]}
    problems = strict.check_strict(findings, {"ui/app/a.ts": 2}, {"ui/app/a.ts"})
    assert problems == ["LOWER ui/app/a.ts: 1 finding(s), its piece says 2; run --write"]


def test_write_lowers_a_count_and_refuses_to_raise_one(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    piece = tmp_path / "ui/baselines/strict/app/a.ts.json"
    lower = {"ui/app/a.ts": [finding("ui/app/a.ts")]}
    assert strict.write(lower, {"ui/app/a.ts": 2}, {"ui/app/a.ts"}) == 0
    assert json.loads(piece.read_text()) == {"count": 1}
    raise_it = {"ui/app/a.ts": [finding("ui/app/a.ts"), finding("ui/app/a.ts", 2)]}
    assert strict.write(raise_it, {"ui/app/a.ts": 1}, {"ui/app/a.ts"}) == 1
    assert json.loads(piece.read_text()) == {"count": 1}


def test_the_old_single_file_layout_is_the_base_at_a_ref(monkeypatch):
    legacy = json.dumps({"files": {"ui/app/a.ts": 5}})
    monkeypatch.setattr(strict, "pieces_at", lambda ref: None)
    monkeypatch.setattr(strict, "git_show", lambda ref, path: legacy if path == strict.LEGACY else None)
    base = strict.base_counts(REF)
    assert base == {"ui/app/a.ts": 5}
    assert strict.check_growth({"ui/app/a.ts": 6}, base, REF) == [
        "GREW ui/app/a.ts: 5 -> 6 since origin/main; fix the findings instead"
    ]


def test_javascript_under_ui_is_refused_except_the_generated_bundle_and_the_kept_files(monkeypatch):
    monkeypatch.setattr(strict, "KEPT_JS", {"ui/app/kept.js": "a reason"})
    monkeypatch.setattr(
        strict,
        "js_on_disk",
        lambda: [
            "ui/app/new.js",
            "ui/app/kept.js",
            "ui/assets/observability-vendors.js",
            "ui/tests/x.cjs",
            "ui/vendor/marked.js",
        ],
    )
    assert strict.check_js() == [
        "ui/app/new.js: JavaScript under ui/; write it as TypeScript (a strict .ts file) instead"
    ]


def test_a_repeated_cast_is_a_hit_and_a_single_cast_is_not():
    assert strict.shift_left_hits("const a = (x as Foo);\n") == []
    assert strict.shift_left_hits("const a = (x as Foo);\nconst b = (y as Foo);\n") == []
    hits = strict.shift_left_hits("const a = (x as Foo);\nconst b = (x as Foo);\n")
    assert [line for line, _ in hits] == [1, 2]
    assert all("repeated cast `x as Foo`" in message for _, message in hits)


def test_a_repeated_non_null_assertion_is_a_hit_and_a_single_one_is_not():
    assert strict.shift_left_hits("a!.b();\n") == []
    hits = strict.shift_left_hits("a!.b();\nc = a!.d;\n")
    assert [line for line, _ in hits] == [1, 2]
    assert all("repeated non-null assertion `a!`" in message for _, message in hits)


def test_any_is_a_hit_wherever_it_is_written():
    for text in ["let v: any = 1;", "const f = (x as any);", "const g = <any>x;", "function h(a, b: any) {}"]:
        hits = strict.shift_left_hits(text)
        assert len(hits) == 1 and "uses `any`" in hits[0][1], text


def test_names_that_only_contain_any_or_are_members_are_not_hits():
    assert strict.shift_left_hits("let anything: string = o.any;\nconst anyway = 1;\n") == []


def test_a_key_named_any_is_not_a_hit():
    assert strict.shift_left_hits("const words = {week: 'Next 7 days', any: 'Has a date'};\n") == []
    assert len(strict.shift_left_hits("const f = (a: string, b: any) => b;\n")) == 1


def test_comments_and_strings_are_not_read():
    text = "// x as Foo\n/* y as Foo */\nconst s = 'z as Foo';\nconst t = `q as Foo`; // any\n"
    assert strict.shift_left_hits(text) == []


def test_import_and_export_aliases_and_as_const_are_not_casts():
    text = "import { a as b } from 'x';\nexport { c as d };\nconst e = [1] as const;\nconst f = [2] as const;\n"
    assert strict.shift_left_hits(text) == []


def test_type_arguments_are_part_of_the_operand_so_two_elements_are_not_one_repeat():
    # Two different receivers with the same selector are two elements: no hit.
    pair = (
        "host.querySelector<HTMLElement>('[data-company-status]')!;\n"
        "form.querySelector<HTMLElement>('[data-company-status]')!;\n"
    )
    assert strict.shift_left_hits(pair) == []
    # The same receiver and selector twice is a repeat, and the key carries the type arguments.
    same = "host.querySelector<HTMLElement>('[x]')!;\nhost.querySelector<HTMLElement>('[x]')!;\n"
    hits = strict.shift_left_hits(same)
    assert [line for line, _ in hits] == [1, 2]
    assert all("host . querySelector < HTMLElement > ( '[x]' )!" in message for _, message in hits)


def test_a_repeated_cast_of_a_call_with_type_arguments_is_a_hit():
    hits = strict.shift_left_hits("const a = b.f<T>(x) as Y;\nconst c = b.f<T>(x) as Y;\n")
    assert [line for line, _ in hits] == [1, 2]
    assert all("repeated cast `b . f < T > ( x ) as Y`" in message for _, message in hits)


def test_a_comparison_before_a_parenthesis_is_not_read_as_type_arguments():
    hits = strict.shift_left_hits("if (a < b && c > (d)!) {}\nif (a < b && c > (d)!) {}\n")
    assert [line for line, _ in hits] == [1, 2]
    assert all("repeated non-null assertion `( d )!`" in message for _, message in hits)


def test_a_statement_header_before_a_call_is_not_part_of_its_operand():
    hits = strict.shift_left_hits("if (a) d.q('p')!;\nif (b) d.q('p')!;\n")
    assert [line for line, _ in hits] == [1, 2]
    assert all("repeated non-null assertion `d . q ( 'p' )!`" in message for _, message in hits)


def test_calls_with_different_receivers_or_arguments_are_not_one_repeat():
    assert strict.shift_left_hits("x.y(1)!;\nz.y(1)!;\n") == []
    assert strict.shift_left_hits("x.y(1)!;\nx.y(2)!;\n") == []


def test_shift_left_reads_only_the_strict_files_without_a_piece(monkeypatch):
    texts = {
        "ui/app/clean.ts": "const a = (x as Foo);\n",
        "ui/app/piece.ts": "const a = (x as Foo);\nconst b = (x as Foo);\n",
        "ui/app/bad.ts": "let v: any;\n",
    }
    monkeypatch.setattr(strict, "ts_on_disk", lambda: sorted(texts))
    monkeypatch.setattr(strict, "read", lambda rel: texts[rel])
    problems = strict.check_shift_left({"ui/app/piece.ts": 2})
    assert problems[0] == "SHIFT-LEFT ui/app/bad.ts: 1 hit(s)"
    assert len(problems) == 2 and problems[1].startswith("    ui/app/bad.ts:1: uses `any`")


def worker_root(tmp_path, monkeypatch, config=None):
    """A checkout holding only the worker config, today's unless `config` replaces it."""
    if config is None:
        config = json.loads((strict.ROOT / strict.WORKER_CONFIG).read_text(encoding="utf-8"))
    (tmp_path / "ui").mkdir()
    (tmp_path / strict.WORKER_CONFIG).write_text(json.dumps(config))
    monkeypatch.setattr(strict, "ROOT", tmp_path)
    return config


def test_a_worker_finding_fails_the_gate_with_no_baseline(monkeypatch):
    found = [strict.Finding("ui/sw.ts", 8, 49, "TS2551", "Property 'skipWaitin' does not exist.")]
    monkeypatch.setattr(strict, "check_worker_config", lambda: [])
    monkeypatch.setattr(strict, "run_tsc", lambda command, config: (found, []))
    problems = strict.check_worker()
    assert problems[0].startswith("WORKER: 1 finding(s)") and "ui/sw.ts:8:49: TS2551" in problems[1]
    assert strict.main(["--worker-only"]) == 1


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda c: c["compilerOptions"].update(strict=False), "strict must be true"),
        (lambda c: c["compilerOptions"].pop("noUncheckedIndexedAccess"), "noUncheckedIndexedAccess must be true"),
        (lambda c: c["compilerOptions"].update(strictNullChecks=False), "strictNullChecks may not be false"),
        (lambda c: c["compilerOptions"].update(allowJs=True), "allowJs must be false"),
        (lambda c: c["compilerOptions"].update(lib=["ES2022", "WebWorker", "DOM"]), "lib must be"),
        (lambda c: c["compilerOptions"].update(types=["node"]), "types must be []"),
        (lambda c: c["compilerOptions"].update(noCheck=True), "noCheck is not allowed"),
        (lambda c: c.update(extends="./tsconfig.json"), "extends not allowed"),
        (lambda c: c.update(include=["./**/*.ts"]), "include must be []"),
    ],
)
def test_weakening_the_worker_config_fails(tmp_path, monkeypatch, change, expected):
    config = worker_root(tmp_path, monkeypatch)
    change(config)
    (tmp_path / strict.WORKER_CONFIG).write_text(json.dumps(config))
    problems = strict.check_worker_config()
    assert any(expected in problem for problem in problems), problems


def test_dropping_the_service_worker_from_the_worker_program_fails(tmp_path, monkeypatch):
    config = worker_root(tmp_path, monkeypatch)
    for files in ([], ["other.ts"], ["sw.ts", "other.ts"]):
        config["files"] = files
        (tmp_path / strict.WORKER_CONFIG).write_text(json.dumps(config))
        assert any("files must be" in p for p in strict.check_worker_config()), files
    (tmp_path / strict.WORKER_CONFIG).unlink()
    assert "cannot be read as JSON" in strict.check_worker_config()[0]


def _tests_root(tmp_path, monkeypatch, config, effective=None):
    """A ui/tests folder holding one script and `config` as its raw tsconfig.json. tsc --showConfig is not run: the gate
    reads `effective` (the raw config when not given) as the options in effect, extends followed."""
    (tmp_path / "ui/tests").mkdir(parents=True)
    (tmp_path / "ui/tests/x.cts").write_text("export {};\n")
    (tmp_path / "ui/tests/tsconfig.json").write_text(json.dumps(config))
    monkeypatch.setattr(strict, "ROOT", tmp_path)
    monkeypatch.setattr(strict, "show_tests_config", lambda: config if effective is None else effective)
    return tmp_path


GOOD_TESTS_CONFIG = {
    "compilerOptions": {
        "strict": True,
        "erasableSyntaxOnly": True,
        "verbatimModuleSyntax": True,
        "moduleDetection": "force",
    },
    "include": ["./**/*.cts"],
}
EXTENDING_TESTS_CONFIG = {"extends": "../../tsconfig.node.json", "include": ["./**/*.cts"]}


def test_a_diagnostic_in_a_test_script_fails_the_tests_gate(tmp_path, monkeypatch):
    _tests_root(tmp_path, monkeypatch, GOOD_TESTS_CONFIG)
    monkeypatch.setattr(
        strict, "run_tests_tsc", lambda: (2, "ui/tests/x.cts(1,7): error TS2322: Type 'number' is not assignable.\n")
    )
    problems = strict.check_tests_gate()
    assert problems[0] == "ui/tests: tsc exited 2; fix every diagnostic (no baseline)"
    assert any("TS2322" in problem for problem in problems)


def test_a_weakened_test_config_fails_without_running_tsc(tmp_path, monkeypatch):
    weak = {"compilerOptions": {"strict": False, "verbatimModuleSyntax": True}, "include": ["./**/*.cts"]}
    _tests_root(tmp_path, monkeypatch, weak)
    monkeypatch.setattr(strict, "run_tests_tsc", lambda: pytest.fail("tsc must not run on a weakened config"))
    problems = strict.check_tests_gate()
    assert any("strict must be true" in p for p in problems)
    assert any("erasableSyntaxOnly must be true" in p for p in problems)


def test_a_declare_global_in_a_test_script_fails_without_running_tsc(tmp_path, monkeypatch):
    root = _tests_root(tmp_path, monkeypatch, GOOD_TESTS_CONFIG)
    (root / "ui/tests/y.cts").write_text("declare global {\n  var post: () => void;\n}\nexport {};\n")
    (root / "ui/tests/z.d.cts").write_text("declare global {\n  var helper: number;\n}\nexport {};\n")
    monkeypatch.setattr(strict, "run_tests_tsc", lambda: pytest.fail("tsc must not run with a declare global"))
    problems = strict.check_tests_gate()
    assert len(problems) == 1 and problems[0].startswith("ui/tests/y.cts: `declare global` is not allowed")


def test_the_test_config_must_keep_module_detection_force(tmp_path, monkeypatch):
    loose = {
        **GOOD_TESTS_CONFIG,
        "compilerOptions": {**GOOD_TESTS_CONFIG["compilerOptions"], "moduleDetection": "auto"},
    }
    _tests_root(tmp_path, monkeypatch, loose)
    monkeypatch.setattr(strict, "run_tests_tsc", lambda: pytest.fail("tsc must not run on a weakened config"))
    assert strict.check_tests_gate() == [
        'ui/tests/tsconfig.json: moduleDetection must be "force" (each script is its own scope)'
    ]


def test_options_inherited_through_extends_count(tmp_path, monkeypatch):
    # The raw file names none of the flags; the base it extends holds them all, as tsconfig.node.json does.
    _tests_root(tmp_path, monkeypatch, EXTENDING_TESTS_CONFIG, effective=GOOD_TESTS_CONFIG)
    monkeypatch.setattr(strict, "run_tests_tsc", lambda: (0, ""))
    assert strict.check_tests_gate() == []


def test_a_base_that_turns_module_detection_off_fails_though_the_raw_file_looks_strict(tmp_path, monkeypatch):
    in_effect = {**GOOD_TESTS_CONFIG, "compilerOptions": {**GOOD_TESTS_CONFIG["compilerOptions"]}}
    in_effect["compilerOptions"].pop("moduleDetection")
    in_effect["compilerOptions"]["verbatimModuleSyntax"] = False
    _tests_root(tmp_path, monkeypatch, GOOD_TESTS_CONFIG, effective=in_effect)
    monkeypatch.setattr(strict, "run_tests_tsc", lambda: pytest.fail("tsc must not run on a weakened config"))
    assert strict.check_tests_gate() == [
        "ui/tests/tsconfig.json: verbatimModuleSyntax must be true (the test scripts' strict rule)",
        'ui/tests/tsconfig.json: moduleDetection must be "force" (each script is its own scope)',
    ]


def test_the_test_config_is_read_with_show_config_from_the_root(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        return subprocess.CompletedProcess(command, 0, json.dumps(GOOD_TESTS_CONFIG), "")

    monkeypatch.setattr(strict, "ROOT", tmp_path)
    monkeypatch.setattr(strict.node_types.subprocess, "run", run)
    assert strict.show_tests_config() == GOOD_TESTS_CONFIG
    [(command, cwd)] = calls
    assert command[-3:] == ["-p", strict.TESTS_CONFIG, "--showConfig"] and cwd == tmp_path


def test_a_test_config_tsc_cannot_read_fails_without_running_tsc(tmp_path, monkeypatch):
    _tests_root(tmp_path, monkeypatch, GOOD_TESTS_CONFIG)

    def unreadable():
        raise strict.node_types.ConfigError("ui/tests/tsconfig.json: tsc --showConfig exited 1: error TS5083")

    monkeypatch.setattr(strict, "show_tests_config", unreadable)
    monkeypatch.setattr(strict, "run_tests_tsc", lambda: pytest.fail("tsc must not run on an unreadable config"))
    assert strict.check_tests_gate() == ["ui/tests/tsconfig.json: tsc --showConfig exited 1: error TS5083"]
