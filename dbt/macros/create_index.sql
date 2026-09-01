{#
    Reusable post-hook index creation. dbt's `table` materialization
    recreates the physical table on every run (drop+create or a
    create-then-swap, depending on adapter internals), so any index from a
    previous run is gone regardless -- but this stays IF NOT EXISTS-guarded
    anyway, both for safety if a model's materialization ever changes to
    incremental, and to match the same guarded-DDL convention used
    throughout sql/*.sql.

    Usage, in a model's config block:
        {{ config(post_hook=[create_index('IX_name', 'col1, col2')]) }}
#}

{% macro create_index(index_name, columns, unique=false, clustered=false) %}
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = '{{ index_name }}' AND object_id = OBJECT_ID('{{ this }}'))
CREATE {{ 'UNIQUE' if unique else '' }} {{ 'CLUSTERED' if clustered else '' }} INDEX {{ index_name }} ON {{ this }} ({{ columns }});
{% endmacro %}
