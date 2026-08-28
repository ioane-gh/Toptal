{#
    Debug helper for generate_schema_name.sql -- prints the schema name dbt
    would actually generate for a given custom schema (or the target schema
    when none is given), without building any model. Run with:

        dbt run-operation print_schema_name
        dbt run-operation print_schema_name --args '{custom_schema_name: datamart}'
#}

{% macro print_schema_name(custom_schema_name=none) %}
    {{ log(generate_schema_name(custom_schema_name, none), info=True) }}
{% endmacro %}
