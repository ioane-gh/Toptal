{#
    Overrides dbt's default generate_schema_name macro.

    dbt's built-in default concatenates <target_schema>_<custom_schema>
    (e.g. dwh_datamart) whenever a model sets a custom `schema` config --
    it never uses the custom schema name on its own. sql/01_schemas.sql
    already created plain `dwh` and `datamart` schemas for this project to
    land models in directly, so that default would silently create an
    unrelated `dwh_datamart` schema instead of using the one that exists.

    With this override: a model with no custom schema config still lands in
    the profile's target schema (dwh); a model with `{{ config(schema=...)
    }}` (or a folder-level `+schema:` in dbt_project.yml) lands in exactly
    that schema, e.g. `datamart`, with no prefix.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}

        {{ default_schema }}

    {%- else -%}

        {{ custom_schema_name | trim }}

    {%- endif -%}

{%- endmacro %}
