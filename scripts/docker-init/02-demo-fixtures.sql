BEGIN;

-- Synthetic data only. These values are intentionally safe to publish.
INSERT INTO customers (
    customer_id,
    full_name,
    email,
    phone,
    date_of_birth,
    customer_tier,
    status,
    created_at
)
VALUES
    (
        'CUST_001',
        'Demo Customer',
        'demo.customer@banking.local',
        '+91 90000 00001',
        DATE '1995-01-15',
        'STANDARD',
        'ACTIVE',
        TIMESTAMPTZ '2026-01-01 09:00:00+00'
    ),
    (
        'CUST_002',
        'Other Customer',
        'other.customer@banking.local',
        '+91 90000 00002',
        DATE '1992-06-10',
        'STANDARD',
        'ACTIVE',
        TIMESTAMPTZ '2026-01-01 09:00:00+00'
    );

-- Password for the demo customer: DemoBank@2026
INSERT INTO customer_credentials (customer_id, password_hash, is_active)
SELECT id,
       '$argon2id$v=19$m=65536,t=3,p=4$Ex6qj3vvdTzBdNKnZsrHFQ$doqzHVb8ZkRl07V93DR3h6rZ7oW2SiFF0KnT+guvY8w',
       true
FROM customers
WHERE customer_id = 'CUST_001';

INSERT INTO accounts (
    account_id,
    customer_id,
    account_type,
    account_number,
    balance,
    currency,
    status,
    created_at
)
VALUES
    (
        'ACC_001',
        (SELECT id FROM customers WHERE customer_id = 'CUST_001'),
        'SAVINGS',
        '0000111122223333',
        75000.00,
        'INR',
        'ACTIVE',
        TIMESTAMPTZ '2026-01-02 09:00:00+00'
    ),
    (
        'ACC_002',
        (SELECT id FROM customers WHERE customer_id = 'CUST_001'),
        'CURRENT',
        '0000444455556666',
        25000.00,
        'INR',
        'ACTIVE',
        TIMESTAMPTZ '2026-01-02 09:00:00+00'
    ),
    (
        'ACC_003',
        (SELECT id FROM customers WHERE customer_id = 'CUST_002'),
        'SAVINGS',
        '0000777788889999',
        12000.00,
        'INR',
        'ACTIVE',
        TIMESTAMPTZ '2026-01-02 09:00:00+00'
    );

INSERT INTO transactions (
    transaction_id,
    account_id,
    transaction_type,
    amount,
    merchant,
    category,
    transaction_date,
    status,
    description
)
VALUES
    (
        'TXN_001',
        (SELECT id FROM accounts WHERE account_id = 'ACC_001'),
        'CREDIT',
        85000.00,
        'Synthetic Salary',
        'Income',
        TIMESTAMPTZ '2026-01-05 09:00:00+00',
        'COMPLETED',
        'Synthetic demo credit'
    ),
    (
        'TXN_002',
        (SELECT id FROM accounts WHERE account_id = 'ACC_001'),
        'DEBIT',
        10000.00,
        'Synthetic Utility',
        'Bills',
        TIMESTAMPTZ '2026-01-08 12:00:00+00',
        'COMPLETED',
        'Synthetic demo debit'
    ),
    (
        'TXN_003',
        (SELECT id FROM accounts WHERE account_id = 'ACC_002'),
        'CREDIT',
        25000.00,
        'Synthetic Opening Balance',
        'Income',
        TIMESTAMPTZ '2026-01-02 09:00:00+00',
        'COMPLETED',
        'Synthetic demo credit'
    );

INSERT INTO service_requests (
    request_id,
    customer_id,
    request_type,
    status,
    created_at,
    completed_at,
    metadata
)
VALUES (
    'SR_001',
    (SELECT id FROM customers WHERE customer_id = 'CUST_001'),
    'STATEMENT',
    'COMPLETED',
    TIMESTAMPTZ '2026-01-04 09:00:00+00',
    TIMESTAMPTZ '2026-01-04 09:10:00+00',
    '{"source": "synthetic-demo"}'::jsonb
);

COMMIT;
