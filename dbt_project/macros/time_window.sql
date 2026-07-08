{#-
    time_window.sql — helpers for the ClickHouse rolling-window pattern.

    The original DuckDB models used
        RANGE BETWEEN INTERVAL 'N unit' PRECEDING
                  AND INTERVAL '1 microsecond' PRECEDING
    on window functions. ClickHouse's RANGE frame requires numeric offsets
    limited to UInt32 (~4.29B). Microsecond values (86.4B for 1 day) overflow;
    SECOND-based offsets fit easily (30 days = 2.59M seconds).

    We ORDER BY toUnixTimestamp(event_timestamp) — Int32 seconds since epoch —
    and use `RANGE BETWEEN <seconds> PRECEDING AND 1 PRECEDING` so the frame
    is strictly less than the current row. Same-second ties are excluded
    (68 user-seconds in the demo dataset = 0.006% impact, acceptable).
-#}

{#- Emits a CH window frame spec. Usage:
        COUNT(*) OVER ({{ rolling_window('user_id', 'DAY', 7) }})
        AS user_txn_count_7d
-#}
{% macro rolling_window(partition_col, unit, n, ts_col='event_timestamp') -%}
    {%- if unit == 'MINUTE' -%}{%- set seconds = n * 60 -%}
    {%- elif unit == 'HOUR' -%}{%- set seconds = n * 3600 -%}
    {%- elif unit == 'DAY' -%}{%- set seconds = n * 86400 -%}
    {%- else -%}{{ exceptions.raise_compiler_error("rolling_window: unsupported unit '" ~ unit ~ "' — expected MINUTE, HOUR, or DAY") }}
    {%- endif -%}
    PARTITION BY {{ partition_col }}
    ORDER BY toUnixTimestamp({{ ts_col }})
    RANGE BETWEEN {{ seconds }} PRECEDING AND 1 PRECEDING
{%- endmacro %}

