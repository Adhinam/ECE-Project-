import asyncio
import asyncpg

async def main():
    try:
        conn = await asyncpg.connect(
            "postgresql://store_intel:store_intel_dev@localhost:5432/store_intel"
        )
        print("SUCCESS")
        await conn.close()
    except Exception as e:
        print("ERROR:", repr(e))

asyncio.run(main())