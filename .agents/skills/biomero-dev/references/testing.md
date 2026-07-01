# Testing (pytest) Conventions

## Runner

From the repo root, using the bundled venv. The terminal cwd can reset between
calls — re-set it to the repo root first if needed.

```powershell
# from the repo root
# Full file
.\venvTest\Scripts\python.exe -m pytest tests/unit/test_slurm_client.py -q
# Narrow slice by name substring
.\venvTest\Scripts\python.exe -m pytest tests/unit/test_slurm_client.py -k default_partition -q
```

Run the narrow slice after each implementation batch, and the full file before
declaring done. There is one expected `SAWarning` about a duplicate declarative
base — it is benign noise, not a failure.

## TDD red/green discipline

The point of TDD is to watch the new tests FAIL before the implementation
exists, then watch them pass after. A test that has never been red proves
nothing.

- Write the tests first and run them — expect failures referencing the missing
  behavior.
- Implement until green.
- If you cannot avoid writing code and tests together, prove the tests are
  meaningful: temporarily neutralize the new code (e.g. guard the new block with
  `if False and ...`), run the behavior tests, confirm the relevant one goes
  red, then restore the real condition and confirm green again.
- Note which tests are *guard* tests (they assert something is NOT done, e.g.
  "GPU partition wins"). Those legitimately stay green even without the new code,
  so they are not evidence the feature works — pair them with at least one
  positive test that goes red.

## Fixture style — mirror what exists

Reuse the established fixtures instead of constructing `SlurmClient` ad hoc.

- `slurm_client`: a fully mocked client (no real SSH). Patches `run`, `put`,
  `open`, `create_session`. Use it as the base for behavior tests.
- Narrowly-scoped behavior fixtures build on `slurm_client` and set only the
  attributes a test needs, e.g. `gpu_workflow_command_client` and
  `default_partition_client`. When adding a feature, add a small fixture in the
  same style rather than repeating setup across tests.
- `slurm_client_from_config_factory`: returns `make_client(config_values=...,
  env_values=...)`. Use it to test `from_config()` parsing and precedence. It
  maps `config_values` onto `('SLURM', key)` and applies `env_values` via
  `patch.dict(os.environ, ...)`. To assert an env var overrides ini, pass both.
- `slurm_configparser_factory`: lower-level mock `ConfigParser` for tests that
  drive `from_config()` directly with `@patch('...configparser.ConfigParser')`.

Behavior tests assert on the returned `cmd` string and/or `env` dict from
`get_workflow_command(...)`. Parsing tests assert on attributes of the client
(e.g. `client.slurm_default_partition`) or on the constructor call kwargs.

## The `test_from_config` exact-kwargs guard

`test_from_config` asserts the FULL keyword-argument call to the `SlurmClient`
constructor (`mock_SlurmClient.assert_called_with(... )`). In that test the
mocked config returns the sentinel string `mv = "configvalue"` for every option,
so most parsed values come back as `mv`.

When you add a constructor parameter, you MUST add a matching line to that
assertion (usually `<param>=mv`), or the test fails with
"expected call not found". Place it in the same position as in the real
`from_config()` constructor call to keep it readable.

## Coverage checklist for a new config option

For a setting like `slurm_default_partition`, add tests for:

- default behavior (unset) produces the current output byte-for-byte;
- the option takes effect when set;
- precedence: a more specific/explicit value wins (per-workflow param or GPU
  partition over a generic fallback);
- env var overrides the ini value (via the factory with both supplied);
- the `test_from_config` exact-kwargs assertion includes the new parameter.
