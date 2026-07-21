"""OutputSchema 配置 CRUD 服务"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutputSchemaConfig
from app.schemas.output_schema import OutputSchemaCreate, OutputSchemaUpdate


async def list_schemas(session: AsyncSession) -> list[OutputSchemaConfig]:
    stmt = select(OutputSchemaConfig).order_by(OutputSchemaConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_schema(session: AsyncSession, schema_id: int) -> OutputSchemaConfig | None:
    return await session.get(OutputSchemaConfig, schema_id)


async def create_schema(session: AsyncSession, payload: OutputSchemaCreate) -> OutputSchemaConfig:
    schema_ = OutputSchemaConfig(
        name=payload.name,
        description=payload.description,
        schema_fields=payload.schema_fields,
    )
    session.add(schema_)
    await session.flush()
    await session.commit()
    await session.refresh(schema_)
    return schema_


async def update_schema(session: AsyncSession, schema_id: int, payload: OutputSchemaUpdate) -> OutputSchemaConfig | None:
    schema_ = await session.get(OutputSchemaConfig, schema_id)
    if schema_ is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(schema_, k, v)
    await session.commit()
    await session.refresh(schema_)
    return schema_


async def delete_schema(session: AsyncSession, schema_id: int) -> bool:
    schema_ = await session.get(OutputSchemaConfig, schema_id)
    if schema_ is None:
        return False
    await session.delete(schema_)
    await session.commit()
    return True
