"""Seed development credentials for the existing customers."""

import asyncio

from sqlalchemy import select

from app.core.database import async_session, engine
from app.core.security import hash_password
from app.models import Customer, CustomerCredential


CUSTOMER_PASSWORDS = {
    "CUST_001": "Akash@123",
    "CUST_002": "Rahul@123",
    "CUST_003": "Priya@123",
    "CUST_004": "Arjun@123",
    "CUST_005": "Sneha@123",
}


async def seed_credentials() -> None:
    seeded_customer_ids = []
    try:
        async with async_session() as session:
            for customer_id, password in CUSTOMER_PASSWORDS.items():
                customer = await session.scalar(
                    select(Customer).where(Customer.customer_id == customer_id)
                )
                if customer is None:
                    raise ValueError(f"Customer {customer_id} was not found")

                credential = await session.scalar(
                    select(CustomerCredential).where(
                        CustomerCredential.customer_id == customer.id
                    )
                )
                if credential is None:
                    session.add(
                        CustomerCredential(
                            customer_id=customer.id,
                            password_hash=hash_password(password),
                            is_active=True,
                        )
                    )
                    seeded_customer_ids.append(customer_id)

            await session.commit()

        if seeded_customer_ids:
            print(f"Seeded credentials for: {', '.join(seeded_customer_ids)}")
        else:
            print("No credentials needed seeding.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_credentials())
