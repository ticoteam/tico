---
name: trace-reachability-of-a-vulnerable-call
description: Decide whether the team's code can reach a vulnerable function, configuration or input path, by tracing from the advisory's trigger to a call site and to untrusted input, and answer yes, no or unknown with what was searched.
---
# Trace reachability of a vulnerable call

When to use: for every advisory that matches a package in a lockfile, before the alert is ranked as reachable or not
reachable. A package being present is not the same as the flaw being reachable.

Steps:
1. Read the advisory's own description of the trigger: the function, the class, the configuration or the input format that
   is vulnerable, and the versions affected. Write the trigger in one line.
2. Find the package's direct or transitive path in the lockfile: which parent brings it in, and whether it is a runtime or a
   dev or test dependency. A transitive package is reachable through its parent's calls, so search for the parent too.
3. Search the code for the trigger: imports of the package, calls to the function, the configuration key, the parser or the
   endpoint named in the advisory. Record each call site with its file and line. Include calls made by a framework on the
   team's behalf (a middleware, a plugin, a template engine).
4. For each call site, ask whether the input reaching the trigger can be controlled by someone outside the team: a request, a
   file upload, a webhook, a message from another service, a stored value a user can edit. Check the path from the entry
   point to the call.
5. Check the exposure of that entry point in `knowledge/exposure.md`: internet-facing, partner-facing, or internal only.
6. Answer one of three:
   - reachable: a call site receives untrusted input on an exposed path. Name the path;
   - not reachable as far as traced: no call site, or only trusted input or a dev-only path. Name what was searched and the
     dynamic patterns that were not checked (reflection, dynamic import, configuration loaded at run time);
   - unknown: a dynamic import, generated code or a framework path that could not be followed. Say so and name the next step.
7. A dev or test dependency is not zero. A build tool or a test runner runs with the team's secrets in CI. Rank it by what
   the job can reach, and say so when it is lower.

Checks before you finish:
- The trigger is written in one line from the advisory itself.
- Each call site has a file and line, or the search that found none is named.
- "Not reachable" always names the search terms and the paths searched.
- No exploit detail beyond the advisory's own description is written down.

Example (invented, for illustration): "Advisory: a parser flaw when a configuration file has a deep nested anchor. The package
is a transitive dependency of `configloader` 3.2 (runtime). Call site: `config/load.py:19` passes the uploaded settings file to
the parser on the public admin API. Reachable: yes, via an exposed upload. Patch: bump `configloader` to 3.2.4."
