{% snapshot customers_snapshot %}

{{
    config(
      target_schema='dwh',
      unique_key='customer_id',
      strategy='timestamp',
      updated_at='updated_at',
    )
}}

SELECT * FROM {{ source('raw_b2b', 'customers') }}

{% endsnapshot %}
