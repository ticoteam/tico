"""A checkout's server builds the web app it serves (backend/web_build.py): only when the build is missing or out of date
with web/, and it still starts, saying what to run, when it cannot build."""

import subprocess

from backend import web_build


def checkout(tmp_path, monkeypatch):
    """A stand-in checkout: web/ with a page and a source file, and build/web as this checkout's own build."""
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "package.json").write_text('{"name": "tico-web"}\n')
    (web / "index.html").write_text("<!doctype html>\n")
    (web / "src" / "main.tsx").write_text("export {};\n")
    (web / "src" / "routeTree.gen.ts").write_text("// generated\n")
    monkeypatch.setattr(web_build, "ROOT", tmp_path)
    return web, tmp_path / "build" / "web"


def test_a_build_is_stale_until_made_from_these_inputs_and_again_once_one_changes(tmp_path, monkeypatch):
    web, out = checkout(tmp_path, monkeypatch)
    assert web_build.stale(web, out)
    out.mkdir(parents=True)
    (out / "index.html").write_text("<!doctype html>\n")
    web_build.stamp_file(out).write_text(web_build.inputs_digest(web) + "\n")
    assert not web_build.stale(web, out)
    (web / "src" / "routeTree.gen.ts").write_text("// generated again\n")  # generated: not an input
    assert not web_build.stale(web, out)
    (web / "src" / "main.tsx").write_text("export const changed = 1;\n")
    assert web_build.stale(web, out)


def test_a_stale_build_is_made_with_npm_and_recorded(tmp_path, monkeypatch):
    web, out = checkout(tmp_path, monkeypatch)
    ran = []

    def fake_run(command, cwd, env, check, timeout):
        ran.append(command[1:])
        if command[1:3] == ["run", "build"]:
            out.mkdir(parents=True)
            (out / "index.html").write_text("<!doctype html>\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(web_build, "npm", lambda: "/usr/local/bin/npm")
    monkeypatch.setattr(web_build.subprocess, "run", fake_run)
    assert web_build.prepare(out, web) is None
    assert ran == [["ci", "--no-audit", "--no-fund"], ["run", "build"]]
    assert not web_build.stale(web, out)
    assert web_build.prepare(out, web) is None and len(ran) == 2  # current: nothing runs again


def test_without_npm_or_with_a_failed_build_the_server_is_told_what_to_run(tmp_path, monkeypatch):
    web, out = checkout(tmp_path, monkeypatch)
    monkeypatch.setattr(web_build, "npm", lambda: None)
    assert "no npm" in web_build.prepare(out, web) and web_build.COMMAND in web_build.prepare(out, web)
    out.mkdir(parents=True)
    (out / "index.html").write_text("<!doctype html>\n")
    assert web_build.prepare(out, web) is None  # a build with no npm beside it (the image's) is served as it is
    (out / "index.html").unlink()

    def failing(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(web_build, "npm", lambda: "/usr/local/bin/npm")
    monkeypatch.setattr(web_build.subprocess, "run", failing)
    assert "did not build (CalledProcessError)" in web_build.prepare(out, web)
    assert web_build.stale(web, out)


def test_a_build_elsewhere_than_this_checkout_s_own_is_left_alone(tmp_path, monkeypatch):
    web, _ = checkout(tmp_path, monkeypatch)
    monkeypatch.setattr(web_build, "npm", lambda: (_ for _ in ()).throw(AssertionError("no build for an image")))
    assert web_build.prepare(tmp_path / "elsewhere", web) is None
