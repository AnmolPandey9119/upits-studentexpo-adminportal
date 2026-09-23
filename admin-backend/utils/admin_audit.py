import json
from database import get_pool


async def audit(action, entity_type=None, entity_id=None, details=None, staff_name=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO admin_audit_logs(action, entity_type, entity_id, details, staff_name)
               VALUES($1,$2,$3,$4::jsonb,$5)""",
            action, entity_type, entity_id, json.dumps(details or {}), staff_name,
        )
