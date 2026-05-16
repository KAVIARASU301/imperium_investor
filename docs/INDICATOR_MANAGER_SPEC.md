# Indicator Manager – Software Requirements & Implementation Plan

## 1) Product Objective
Build a production-grade Indicator Manager that separates:
- **Indicator Types (plugins)** from
- **Indicator Instances (active, user-configured chart objects)**.

The manager must support adding the same indicator type multiple times, editing each instance independently, and removing any instance without side effects.

## 2) UX Requirements
The Indicator Manager UI is a long dropdown/popup/panel with two sections.

### Selected Indicators (top)
Each row is one active instance and includes:
- display name
- compact settings summary (e.g., `EMA (20)`)
- **Edit** action
- **Remove** action

### Available Indicators (bottom)
Each row is one registered type and includes:
- display name
- **Add** action

## 3) Functional Requirements
1. **Add** creates a new instance with indicator defaults and activates it immediately.
2. Duplicate adds of the same indicator type create independent instances.
3. **Edit** opens that indicator’s own settings dialog/schema-driven form.
4. Saving edits validates settings, persists them, recalculates, and redraws chart.
5. **Remove** deactivates and deletes only the targeted instance and cleans resources.

## 4) Domain Model

### IndicatorType (plugin)
- `type_id` (stable unique id)
- `display_name`
- `category` (optional)
- `default_settings`
- `settings_schema` (or custom dialog hook)
- `validate(settings) -> normalized_settings`
- `calculate(data, settings) -> outputs`
- `render_spec(outputs, settings)`
- `pane_mode` (`overlay`/`separate`)
- `supported_sources`

### IndicatorInstance
- `instance_id` (UUID)
- `type_id`
- `display_name_snapshot`
- `settings`
- `enabled`
- `z_order` / `order_index`
- `pane_id`
- lifecycle state (`active|error|removed`)

## 5) Architecture
1. **Indicator Registry / Plugin Loader**
   - Discovers and registers indicator types.
2. **Selected Instance Manager**
   - Creates, stores, updates, and removes instances.
3. **Config Dialog System**
   - Primarily schema-driven; supports custom dialogs as extension path.
4. **Calculation/Render Engine**
   - Isolates computation failures per instance.
5. **Persistence Layer**
   - Stores active instances + settings + order/pane metadata.
6. **Validation & Error Layer**
   - Enforces schema/type/range constraints and safe failure behavior.

## 6) Persistence Contract
Persist by chart/workspace:
- `selected_indicator_instances[]`
  - `instance_id`
  - `type_id`
  - `settings`
  - `enabled`
  - `order_index`
  - `pane_id`
  - visual style settings

System restores exact state on startup/reload.

## 7) Error-Handling Contract
- Invalid user input is blocked before save.
- Plugin load failure marks only that type unavailable.
- Calculation/render failure in one instance does not crash chart/app.
- Corrupt persisted settings fall back to defaults with user notice.

## 8) Performance Requirements
- Compute indicators per instance in isolation.
- Support multiple repeated instances (e.g., EMA 20/50/200).
- Debounce rapid edit/save operations.
- Minimize full-chart redraws (prefer partial updates where possible).

## 9) Incremental Implementation Roadmap

### Phase 1 (Core)
- Add registry + type/instance models.
- Implement two-section manager UI.
- Add/add-duplicate/edit/remove flows.
- Persist instance list and settings.

### Phase 2 (Robustness)
- Schema-driven forms + validation framework.
- Per-instance fault isolation and error surfacing.
- Migration from legacy fixed-indicator settings.

### Phase 3 (Scale)
- Search/filter/categories/favorites.
- Reorder, enable/disable, duplicate configured instance.
- Presets/templates import-export.

## 10) Acceptance Criteria
- User can add EMA 3x and configure 20/50/200 independently.
- Each instance appears separately and renders simultaneously.
- Removing one instance leaves other instances untouched.
- Edited settings persist and survive restart.
- Broken plugin/instance does not crash app.
