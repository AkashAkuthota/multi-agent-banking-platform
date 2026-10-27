BEGIN;

DO $$
DECLARE
    anchor_account RECORD;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM accounts
        GROUP BY customer_id
        HAVING count(*) >= 2
    ) THEN
        SELECT customer_id, currency
        INTO anchor_account
        FROM accounts
        WHERE account_id = 'ACC_001';

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'ACC_001 is required to seed the demo transfer account';
        END IF;

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
        VALUES (
            'ACC_DEMO_002',
            anchor_account.customer_id,
            'SAVINGS',
            'XXXXXX9002',
            25000.00,
            anchor_account.currency,
            'ACTIVE',
            now()
        );
    END IF;
END;
$$;

COMMIT;
