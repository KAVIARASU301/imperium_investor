# Indicator Library Guide

## Goal
Build indicators as reusable, user-configurable components (not hardcoded toggles).

## UX contract
- Indicator menu has two sections:
  - **Selected**: active indicator instances (supports edit/remove).
  - **Available**: catalog of indicator types with **+** action.
- A single indicator type can have multiple instances (e.g., EMA 20, EMA 50, EMA 200).

## Required indicator interface
Each indicator plugin must define:
1. `type_id` (stable, e.g. `moving_average`)
2. `instance_id` (unique per selected instance)
3. `display_name`
4. `default_config`
5. `validate_config(config) -> normalized_config`
6. `calculate(df, config) -> series[]` (time/value points)
7. `style_schema` (color, width, line style)
8. `edit_dialog(parent, current_config) -> new_config | None`

## Data model
Persist selected indicators in global chart settings:
```json
{
  "moving_average_configs": [
    {"id":"ema_1","period":20,"color":"#2962ff","thickness":1.2,"line_style":"solid"}
  ]
}
```

## Rendering contract
- Python computes indicator series and sends to JS in payload.
- JS renderer uses per-instance styling from config.
- Visibility and geometry should auto-recompute when indicators are added/removed.

## Production checklist
- Input validation (period bounds, style values)
- Backward compatibility migration from legacy hardcoded EMA keys
- Unit tests for calculate/validate functions
- Snapshot tests for payload shape
- Graceful failure if one indicator instance fails (do not crash whole chart)
