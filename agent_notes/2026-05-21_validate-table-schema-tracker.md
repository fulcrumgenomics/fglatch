# Tracker: `LatchRecordModel.validate_table_schema` — stacked PR series

Closes #42 when the whole stack lands.

This is a tracking document. It is **not** intended to merge — leave the PR as draft and close it once every child PR lands.

## Stack

Each child PR is stacked on the previous one. Mark complete when merged.

- [x] **#47** — `feat(registry): add schema validation error types` (~130 lines, base: `main`)
- [ ] **#48a** — `feat(registry): add schema validation dispatcher + missing/extra columns` (~170 lines, base: #47)
- [ ] **#48b** — `feat(registry): compare primitive types and nullability` (~210 lines, base: #48a)
- [ ] **#49** — `feat(registry): expose validate_table_schema classmethod on LatchRecordModel` (~105 lines, base: #48b)
- [ ] **#50a** — `feat(registry): validate enum columns` (~170 lines, base: #49)
- [ ] **#50b** — `feat(registry): validate blob columns` (~170 lines, base: #50a)
- [ ] **#51a** — `feat(registry): validate array columns` (~210 lines, base: #50b)
- [ ] **#51b** — `feat(registry): validate link columns` (~110 lines, base: #51a)
- [ ] **#52** — `feat(registry): validate schema in from_record + integration tests` (~250 lines, base: #51b)

`#48` originally bundled the dispatcher + nullability + primitive comparison; per @msto, decomposed into #48a (enumerate fields) and #48b (compare types). `#50` and `#51` split per-column-kind.

## Cross-cutting refactors (apply across the whole stack)

- [ ] **Drop `Any` where possible.** Use `TypeAnnotation` (imported from `fgmetric._typing_extensions` — private import for now; TODO: switch to the public API once @msto promotes it).
- [ ] **Reuse `fgmetric` type introspection.** Replace `_is_nullable` → `is_optional`, `_unwrap_none` → `unpack_optional`, list checks → `is_list` / `has_origin`. Adds `fgmetric>=0.3` as a runtime dep. (Possible future extraction into a dedicated `fgtyping` package — trigger is the second non-metric consumer.)
- [ ] **Rename `expected` / `actual` on `SchemaMismatch`.** Both names are ambiguous — "expected by whom?" New schema: two optional fields, `model_type: TypeAnnotation | None` and `column_type: TypeAnnotation | None`, plus two optional name fields, `model_field: str | None` and `column_name: str | None`. Which slots are populated depends on `kind`. Messages derive from whatever is populated.

## Per-PR review fixes

### #47 — error types

- [ ] Convert `SchemaMismatch.kind` from `Literal[...]` to a `StrEnum` (declared as a nested class variable on `SchemaMismatch` for discoverability).
- [ ] Make `SchemaMismatch.message` a Pydantic computed field, dispatched on `kind` (and on the kind StrEnum). No more caller-supplied messages.
- [ ] Tests: drop `test_schema_mismatch_constructs` (just exercises `BaseModel.__init__`) and `test_registry_table_schema_error_carries_mismatches` (same). Keep `test_registry_table_schema_error_is_value_error` but pass an empty `mismatches=[]`. Add a test that asserts the computed `message` content per kind.

### #48a — dispatcher + missing/extra (split from #48)

- [ ] Replace `_is_nullable` / `_unwrap_none` with `fgmetric.is_optional` / `unpack_optional`.
- [ ] Replace `Any` with `TypeAnnotation` on the dispatcher's signatures.
- [ ] Drop the `_describe_type` helper — keep literal annotation strings everywhere (per @msto: "explicit is better than implicit").
- [ ] Drop the `_type_mismatch` helper — `SchemaMismatch` derives `message` itself from `kind` + types.
- [ ] Re-evaluate the `TYPE_CHECKING`-only forward ref `"type[LatchRecordModel]"` on `_validate_table_schema` — drop if unnecessary.

### #48b — primitive + nullability (new, split from #48)

- [ ] Decide whether `_compare_field_to_column` / `_compare_unwrapped` should return `SchemaMismatch | None` instead of `list[SchemaMismatch]`. They never return >1; the list shape was just for `.extend()` ergonomics.
- [ ] Guard against missing `allowEmpty` key on the upstream type dict (or document that the SDK always populates it).

### #49 — classmethod

- [ ] Confirm whether `table.load()` is required, and what it does (network round-trip? cache?). Drop or document.
- [ ] Reconsider raise-via-classmethod vs `_validate_table_schema` raising directly. If the helper raises, the classmethod is a one-liner.

### #50a — enum (split from #50)

- [ ] Fix enum comparison. SDK constructs the column's type as `Enum("Enum", members)`, which puts the Registry strings in `.name` and auto-ints in `.value`. The model side (Python convention) puts the Python identifier in `.name` (e.g. `"FOO"`) and the user-assigned string in `.value` (e.g. `"Foo"`). Correct comparison: **Registry `.name` ↔ Model `.value`**. The current test fixture happened to use identifiers identical to values (`ALPHA = "ALPHA"`), masking the bug.
- [ ] Decide how to handle `auto()`-valued model enums (where `.value` is a meaningless int): either reject them with a clear error, or fall back to `.name`-on-both-sides with a documented caveat. Recommend rejection — the user should be explicit about which string the enum maps to.
- [ ] Rename `expected`/`actual` per cross-cutting item.

### #50b — blob (new, split from #50)

- [ ] No additional notes beyond cross-cutting items.

### #51a — array (split from #51)

- [ ] Use `fgmetric.is_list` / `has_origin` for the list check.
- [ ] Use `fgmetric.has_optional_elements` if it simplifies the element-nullability decision.

### #51b — link (new, split from #51)

- [ ] Drop the local-import dance in `_is_latch_record_model_subclass` if a structural check (e.g. `isinstance(t, type) and hasattr(t, "model_fields")` or a `Protocol`) works.

### #52 — `from_record` + integration

- [ ] Confirm the integration fixture is provisioned and the env vars are documented in the README.

## Backup

Original linear history of `feat/validate-table-schema` is preserved locally as `backup/feat-validate-table-schema-orig` (not pushed). Restore from there if a rebase goes sideways.
